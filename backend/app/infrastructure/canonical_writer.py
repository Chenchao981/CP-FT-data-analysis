from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text

from app.cleaners.huahong_dcp import HuaHongDcpFile, HuaHongDcpParser
from app.infrastructure.sql_bin_mapping_materializer import (
    materialize_processing_run_bin_mappings,
)
from app.infrastructure.sql_spec_evaluation_materializer import (
    materialize_processing_run_spec_evaluations,
)
from app.infrastructure.stage_fact_repository import insert_measurements, insert_units
from app.infrastructure.stage_run_details import persist_stage_run_details


class CanonicalWriteError(ValueError):
    """Raised before canonical data is written when an explicit contract is missing."""


@dataclass(frozen=True, slots=True)
class SourceRegistration:
    sha256: str
    file_size: int
    canonical_storage_uri: str
    original_file_name: str
    import_batch_id: int | None = None
    received_by: str | None = None
    received_channel: str = "MANUAL"


@dataclass(frozen=True, slots=True)
class SourceReceipt:
    source_file_id: int
    receipt_id: int
    is_duplicate_receipt: bool


@dataclass(frozen=True, slots=True)
class HuaHongWriteContext:
    source_file_id: int
    job_id: int
    parser_profile_id: int
    supplier_id: int
    product_id: int | None
    program_version_id: int
    test_item_ids: Mapping[str, int]
    canonical_model_version: str = "1.0"


@dataclass(frozen=True, slots=True)
class CanonicalWriteResult:
    processing_run_id: int
    test_run_id: int
    unit_count: int
    measurement_count: int


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _positive(value: int, field: str) -> None:
    if value <= 0:
        raise CanonicalWriteError(f"{field} must be a positive database identity")


class SourceFileRepository:
    """Register immutable source content and a receipt without storing file bytes in SQL."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def register(self, registration: SourceRegistration) -> SourceReceipt:
        if len(registration.sha256) != 64 or any(
            char not in "0123456789abcdefABCDEF" for char in registration.sha256
        ):
            raise CanonicalWriteError("source SHA256 must contain exactly 64 hex characters")
        if registration.file_size <= 0:
            raise CanonicalWriteError("source file size must be positive")
        if not registration.canonical_storage_uri.strip():
            raise CanonicalWriteError("canonical storage URI is required")
        if not registration.original_file_name.strip():
            raise CanonicalWriteError("original file name is required")

        digest = registration.sha256.lower()
        with self._engine.begin() as connection:
            existing = (
                connection.execute(
                    text(
                        "SELECT source_file_id, file_size, canonical_storage_uri "
                        "FROM ingestion.source_file WITH (UPDLOCK, HOLDLOCK) "
                        "WHERE sha256=:sha256"
                    ),
                    {"sha256": digest},
                )
                .mappings()
                .one_or_none()
            )
            duplicate = existing is not None
            if existing is None:
                source_file_id = int(
                    connection.execute(
                        text(
                            "INSERT ingestion.source_file(sha256,file_size,canonical_storage_uri,metadata_json) "
                            "OUTPUT INSERTED.source_file_id "
                            "VALUES(:sha256,:file_size,:storage_uri,:metadata_json)"
                        ),
                        {
                            "sha256": digest,
                            "file_size": registration.file_size,
                            "storage_uri": registration.canonical_storage_uri,
                            "metadata_json": _json({"content_identity": "SHA256"}),
                        },
                    ).scalar_one()
                )
            else:
                if int(existing["file_size"]) != registration.file_size:
                    raise CanonicalWriteError("existing source SHA256 has a conflicting file size")
                source_file_id = int(existing["source_file_id"])

            receipt_id = int(
                connection.execute(
                    text(
                        "INSERT ingestion.source_file_receipt("
                        "source_file_id,import_batch_id,original_file_name,received_by,"
                        "received_channel,is_duplicate_receipt,metadata_json) "
                        "OUTPUT INSERTED.receipt_id VALUES("
                        ":source_file_id,:import_batch_id,:original_file_name,:received_by,"
                        ":received_channel,:is_duplicate_receipt,:metadata_json)"
                    ),
                    {
                        "source_file_id": source_file_id,
                        "import_batch_id": registration.import_batch_id,
                        "original_file_name": registration.original_file_name,
                        "received_by": registration.received_by,
                        "received_channel": registration.received_channel,
                        "is_duplicate_receipt": int(duplicate),
                        "metadata_json": _json(
                            {"canonical_storage_uri": registration.canonical_storage_uri}
                        ),
                    },
                ).scalar_one()
            )
        return SourceReceipt(source_file_id, receipt_id, duplicate)


class HuaHongCanonicalWriter:
    """Write one strictly parsed HuaHong wafer using approved MDM identities only."""

    def __init__(self, engine: Engine, *, measurement_batch_size: int = 1000) -> None:
        if measurement_batch_size <= 0:
            raise ValueError("measurement_batch_size must be positive")
        self._engine = engine
        self._measurement_batch_size = measurement_batch_size

    @staticmethod
    def _validate_contract(file: HuaHongDcpFile, context: HuaHongWriteContext) -> None:
        for field in (
            "source_file_id",
            "job_id",
            "parser_profile_id",
            "supplier_id",
            "program_version_id",
        ):
            _positive(int(getattr(context, field)), field)
        if context.product_id is not None:
            _positive(context.product_id, "product_id")
        expected = set(file.parameters)
        supplied = set(context.test_item_ids)
        if supplied != expected:
            missing = sorted(expected - supplied)
            extra = sorted(supplied - expected)
            raise CanonicalWriteError(
                f"test item mapping must exactly match the parsed schema; missing={missing}, extra={extra}"
            )
        if any(value <= 0 for value in context.test_item_ids.values()):
            raise CanonicalWriteError("test item mappings must use positive database identities")
        if not context.canonical_model_version.strip():
            raise CanonicalWriteError("canonical model version is required")

    def write(self, file: HuaHongDcpFile, context: HuaHongWriteContext) -> CanonicalWriteResult:
        self._validate_contract(file, context)
        with self._engine.begin() as connection:
            boundary = (
                connection.execute(
                    text(
                        "SELECT sf.sha256, pj.source_file_id AS job_source_file_id, "
                        "s.active AS supplier_active, "
                        "CASE WHEN :product_id IS NULL THEN CAST(1 AS bit) ELSE p.active END AS product_active, "
                        "tp.active AS program_active, pj.status AS job_status, "
                        "pp.active AS parser_active, pp.format_code, pp.parser_version, "
                        "tpv.program_version_id "
                        "FROM ingestion.source_file sf "
                        "JOIN ingestion.processing_job pj ON pj.job_id=:job_id "
                        "JOIN ingestion.cleaner_release cr ON cr.cleaner_release_id=pj.cleaner_release_id "
                        "JOIN ingestion.format_profile fp ON fp.format_profile_id=cr.format_profile_id "
                        "JOIN mdm.supplier s ON s.supplier_id=:supplier_id "
                        "LEFT JOIN mdm.product p ON p.product_id=:product_id "
                        "JOIN ingestion.parser_profile pp ON pp.parser_profile_id=:parser_profile_id "
                        "JOIN mdm.test_program_version tpv ON tpv.program_version_id=:program_version_id "
                        "JOIN mdm.test_program tp ON tp.test_program_id=tpv.test_program_id "
                        "WHERE sf.source_file_id=:source_file_id "
                        "AND tp.supplier_id=:supplier_id "
                        "AND ((:product_id IS NULL AND tp.product_id IS NULL) OR tp.product_id=:product_id) "
                        "AND tp.test_stage='CP' "
                        "AND (pp.supplier_id IS NULL OR pp.supplier_id=:supplier_id) "
                        "AND pp.test_stage='CP' "
                        "AND cr.status='RELEASED' AND fp.status='RELEASED' "
                        "AND fp.format_code='HUAHONG_DCP_TXT' AND fp.test_stage='CP' "
                        "AND (fp.supplier_id IS NULL OR fp.supplier_id=:supplier_id)"
                    ),
                    {
                        "job_id": context.job_id,
                        "supplier_id": context.supplier_id,
                        "product_id": context.product_id,
                        "parser_profile_id": context.parser_profile_id,
                        "program_version_id": context.program_version_id,
                        "source_file_id": context.source_file_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if boundary is None:
                raise CanonicalWriteError("one or more explicit source/MDM identities do not exist")
            if str(boundary["sha256"]).lower() != file.source_sha256.lower():
                raise CanonicalWriteError("parsed content SHA256 does not match the registered source")
            if int(boundary["job_source_file_id"]) != context.source_file_id:
                raise CanonicalWriteError("processing job does not reference the registered source")
            if not all(
                bool(boundary[field])
                for field in (
                    "supplier_active",
                    "product_active",
                    "program_active",
                    "parser_active",
                )
            ):
                raise CanonicalWriteError(
                    "supplier, optional product, program, and parser profile must be active"
                )
            if boundary["job_status"] != "RUNNING":
                raise CanonicalWriteError("processing job must be RUNNING before canonical write")
            if boundary["format_code"] != "HUAHONG_DCP_TXT":
                raise CanonicalWriteError("parser profile is not approved for HuaHong DCP")
            if str(boundary["parser_version"]) != HuaHongDcpParser.profile_version:
                raise CanonicalWriteError("parser profile version does not match the active parser")

            item_rows = (
                connection.execute(
                    text(
                        "SELECT test_item_id, raw_item_name FROM mdm.test_item_definition "
                        "WHERE program_version_id=:program_version_id"
                    ),
                    {"program_version_id": context.program_version_id},
                )
                .mappings()
                .all()
            )
            if len(item_rows) != len(file.parameters) or any(
                row["raw_item_name"] is None for row in item_rows
            ):
                raise CanonicalWriteError(
                    "approved program version must define each parsed test item exactly once"
                )
            approved_items = {str(row["raw_item_name"]): int(row["test_item_id"]) for row in item_rows}
            if len(approved_items) != len(item_rows):
                raise CanonicalWriteError(
                    "approved program version contains duplicate raw test item names"
                )
            if dict(context.test_item_ids) != approved_items:
                raise CanonicalWriteError(
                    "test item mapping does not exactly match the approved program version"
                )

            processing_run_id = int(
                connection.execute(
                    text(
                        "INSERT ingestion.processing_run("
                        "job_id,source_file_id,parser_profile_id,parser_version,"
                        "canonical_model_version,status,row_count_input,metadata_json) "
                        "OUTPUT INSERTED.processing_run_id VALUES("
                        ":job_id,:source_file_id,:parser_profile_id,:parser_version,"
                        ":canonical_model_version,'NORMALIZING',:row_count_input,:metadata_json)"
                    ),
                    {
                        "job_id": context.job_id,
                        "source_file_id": context.source_file_id,
                        "parser_profile_id": context.parser_profile_id,
                        "parser_version": str(boundary["parser_version"]),
                        "canonical_model_version": context.canonical_model_version,
                        "row_count_input": file.row_count,
                        "metadata_json": _json(
                            {"schema_id": file.schema_id, "source_name": file.source_name}
                        ),
                    },
                ).scalar_one()
            )

            source_started_local = datetime.combine(file.source_date, file.source_time)
            test_run_id = int(
                connection.execute(
                    text(
                        "INSERT test.test_run("
                        "processing_run_id,supplier_id,product_id,program_version_id,test_stage,"
                        "lot_id,wafer_id,source_started_local,timezone_resolution,timestamp_source,metadata_json) "
                        "OUTPUT INSERTED.run_id VALUES("
                        ":processing_run_id,:supplier_id,:product_id,:program_version_id,'CP',"
                        ":lot_id,:wafer_id,:source_started_local,'UNKNOWN','SOURCE_FILE',:metadata_json)"
                    ),
                    {
                        "processing_run_id": processing_run_id,
                        "supplier_id": context.supplier_id,
                        "product_id": context.product_id,
                        "program_version_id": context.program_version_id,
                        "lot_id": file.business_lot_id,
                        "wafer_id": str(int(file.wafer_number)),
                        "source_started_local": source_started_local,
                        "metadata_json": _json(
                            {
                                "source_lot_run": file.lot_number,
                                "source_sha256": file.source_sha256,
                                "source_program_name": file.program_name,
                                "source_timezone_unresolved": True,
                            }
                        ),
                    },
                ).scalar_one()
            )

            measurement_rows: list[dict[str, object]] = []
            measurement_count = 0
            for unit in file.units:
                logical_key = (
                    f"CP:{file.business_lot_id}:{int(file.wafer_number)}:{unit.x}:{unit.y}"
                )
                unit_id = insert_units(
                    connection,
                    "CP",
                    [
                        {
                            "run_id": test_run_id,
                            "logical_unit_key": logical_key,
                            "unit_sequence": unit.sequence,
                            "wafer_id": str(int(file.wafer_number)),
                            "x_coord": unit.x,
                            "y_coord": unit.y,
                            "soft_bin": str(unit.soft_bin),
                            "overall_result": unit.overall_result,
                            "source_row_no": unit.source_row_no,
                            "metadata_json": _json(
                                {
                                    "source_lot_run": file.lot_number,
                                    "source_sha256": file.source_sha256,
                                }
                            ),
                        }
                    ],
                )[0]
                for column_index, parameter, measurement in zip(
                    file.source_column_indexes,
                    file.parameters,
                    unit.measurements,
                    strict=True,
                ):
                    measurement_rows.append(
                        {
                            "unit_id": unit_id,
                            "test_item_id": context.test_item_ids[parameter],
                            "value_numeric": measurement.value_numeric,
                            "raw_value": measurement.raw,
                            "measurement_status": measurement.status,
                            "source_column_index": column_index,
                        }
                    )
                    if len(measurement_rows) >= self._measurement_batch_size:
                        insert_measurements(connection, "CP", measurement_rows)
                        measurement_count += len(measurement_rows)
                        measurement_rows = []
            if measurement_rows:
                insert_measurements(connection, "CP", measurement_rows)
                measurement_count += len(measurement_rows)

            persist_stage_run_details(connection, processing_run_id=processing_run_id)

            materialize_processing_run_bin_mappings(
                connection,
                processing_run_id=processing_run_id,
            )
            materialize_processing_run_spec_evaluations(
                connection,
                processing_run_id=processing_run_id,
            )

            connection.execute(
                text(
                    "UPDATE ingestion.processing_run SET status='READY',"
                    "unit_count_output=:unit_count,measurement_count_output=:measurement_count,"
                    "finished_at_utc=SYSUTCDATETIME() WHERE processing_run_id=:processing_run_id"
                ),
                {
                    "unit_count": file.row_count,
                    "measurement_count": measurement_count,
                    "processing_run_id": processing_run_id,
                },
            )
        return CanonicalWriteResult(
            processing_run_id=processing_run_id,
            test_run_id=test_run_id,
            unit_count=file.row_count,
            measurement_count=measurement_count,
        )
