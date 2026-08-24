from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from app.infrastructure.existing_cleaner_runner import CleanerArtifact


class CpCsvTripletError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CpSpecItem:
    name: str
    unit: str | None
    lsl: float | None
    usl: float | None
    raw_lsl: str | None
    raw_usl: str | None
    test_condition: str | None


@dataclass(frozen=True, slots=True)
class CpCleanedRow:
    lot_id: str
    wafer_id: str
    raw_wafer_id: str
    seq_no: int
    bin_value: str
    x: int
    y: int
    values: tuple[str, ...]
    source_row_no: int

    @property
    def logical_key(self) -> str:
        return f"CP:{self.lot_id}:{self.wafer_id}:{self.x}:{self.y}"


@dataclass(frozen=True, slots=True)
class CpCsvTriplet:
    product_name: str
    parameters: tuple[str, ...]
    spec_items: tuple[CpSpecItem, ...]
    rows: tuple[CpCleanedRow, ...]
    spec_sha256: str
    source_paths: tuple[str, ...]
    pass_count: int


@dataclass(frozen=True, slots=True)
class CpCanonicalImportResult:
    processing_run_id: int
    dataset_id: int
    dataset_version_no: int
    spec_set_id: int
    unit_count: int
    measurement_count: int


def _number(raw: str, *, field: str, allow_blank: bool = False) -> float | None:
    value = raw.strip()
    if not value and allow_blank:
        return None
    try:
        return float(Decimal(value))
    except (InvalidOperation, ValueError) as exc:
        raise CpCsvTripletError(f"{field} is not numeric: {raw!r}") from exc


def _wafer_id(raw: str) -> str:
    value = raw.strip()
    if value.isdigit():
        return str(int(value))
    return value


def _artifact_paths(
    artifacts: tuple[CleanerArtifact, ...], role: str
) -> tuple[Path, ...]:
    paths = tuple(
        sorted(
            (Path(item.path) for item in artifacts if item.role == role),
            key=lambda path: path.name.casefold(),
        )
    )
    if not paths:
        raise CpCsvTripletError(f"CP output is missing {role} CSV")
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise CpCsvTripletError(f"CP output file does not exist: {missing[0]}")
    return paths


def _read_spec(path: Path) -> tuple[tuple[str, ...], tuple[CpSpecItem, ...]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    if len(rows) < 4 or not rows[0] or rows[0][0].strip() != "Parameter":
        raise CpCsvTripletError("CP spec CSV has an unsupported header")
    parameters = tuple(value.strip() for value in rows[0][1:])
    if not parameters or any(not value for value in parameters):
        raise CpCsvTripletError("CP spec CSV contains a blank parameter")
    if len(set(parameters)) != len(parameters):
        raise CpCsvTripletError("CP spec CSV contains duplicate parameters")
    indexed = {row[0].strip(): row[1:] for row in rows[1:4] if row}
    if set(indexed) != {"Unit", "LimitU", "LimitL"}:
        raise CpCsvTripletError("CP spec CSV must contain Unit, LimitU and LimitL")
    conditions: list[list[str]] = [[] for _ in parameters]
    for row in rows[4:]:
        values = row[1:]
        for index, value in enumerate(values[: len(parameters)]):
            normalized = value.strip()
            if normalized:
                conditions[index].append(normalized)
    items: list[CpSpecItem] = []
    for index, name in enumerate(parameters):
        unit = indexed["Unit"][index].strip() or None
        raw_usl = indexed["LimitU"][index].strip() or None
        raw_lsl = indexed["LimitL"][index].strip() or None
        items.append(
            CpSpecItem(
                name=name,
                unit=unit,
                lsl=_number(raw_lsl or "", field=f"{name} LSL", allow_blank=True),
                usl=_number(raw_usl or "", field=f"{name} USL", allow_blank=True),
                raw_lsl=raw_lsl,
                raw_usl=raw_usl,
                test_condition=" | ".join(conditions[index]) or None,
            )
        )
    parameter_items = tuple(item for item in items if item.name != "CONT")
    return tuple(item.name for item in parameter_items), parameter_items


def parse_cp_csv_triplet(
    artifacts: tuple[CleanerArtifact, ...],
) -> CpCsvTriplet:
    cleaned_paths = _artifact_paths(artifacts, "cleaned")
    yield_paths = _artifact_paths(artifacts, "yield")
    spec_path = _artifact_paths(artifacts, "spec")[0]
    spec_parameters, declared_spec_items = _read_spec(spec_path)
    base_columns = ("Lot_ID", "Wafer_ID", "Seq", "Bin", "X", "Y")
    parameters: tuple[str, ...] | None = None
    source_parameter_columns: tuple[str, ...] | None = None
    cleaned_rows: list[CpCleanedRow] = []
    seen_keys: set[str] = set()
    for path in cleaned_paths:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = tuple(reader.fieldnames or ())
            if columns[: len(base_columns)] != base_columns:
                raise CpCsvTripletError(
                    f"CP cleaned identity columns are unsupported: {path.name}"
                )
            measured_columns = columns[len(base_columns) :]
            if not measured_columns or len(set(measured_columns)) != len(
                measured_columns
            ):
                raise CpCsvTripletError(
                    f"CP cleaned measurement columns are invalid: {path.name}"
                )
            if parameters is None:
                source_parameter_columns = measured_columns
                parameters = tuple(
                    name for name in measured_columns if name != "CONT"
                )
                if parameters != spec_parameters:
                    raise CpCsvTripletError(
                        "CP cleaned parameters do not match first Spec after excluding CONT"
                    )
            if measured_columns != source_parameter_columns:
                raise CpCsvTripletError(
                    f"CP cleaned measurement columns differ: {path.name}"
                )
            for source_row_no, row in enumerate(reader, start=2):
                lot_id = (row["Lot_ID"] or "").strip()
                raw_wafer_id = (row["Wafer_ID"] or "").strip()
                wafer_id = _wafer_id(raw_wafer_id)
                if not lot_id or not raw_wafer_id:
                    raise CpCsvTripletError(
                        f"CP cleaned row {source_row_no} has no Lot/Wafer"
                    )
                try:
                    parsed = CpCleanedRow(
                        lot_id=lot_id,
                        wafer_id=wafer_id,
                        raw_wafer_id=raw_wafer_id,
                        seq_no=int(row["Seq"]),
                        bin_value=str(int(row["Bin"])),
                        x=int(row["X"]),
                        y=int(row["Y"]),
                        values=tuple((row[name] or "").strip() for name in parameters),
                        source_row_no=source_row_no,
                    )
                except (TypeError, ValueError) as exc:
                    raise CpCsvTripletError(
                        f"CP cleaned row {source_row_no} has invalid identity values"
                    ) from exc
                if parsed.logical_key in seen_keys:
                    raise CpCsvTripletError(
                        f"duplicate CP Die coordinate: {parsed.logical_key}"
                    )
                seen_keys.add(parsed.logical_key)
                for name, raw in zip(parameters, parsed.values, strict=True):
                    _number(raw, field=f"row {source_row_no} {name}", allow_blank=True)
                cleaned_rows.append(parsed)
    if not cleaned_rows:
        raise CpCsvTripletError("CP cleaned CSV contains no Die rows")
    assert parameters is not None

    expected_counts: Counter[tuple[str, str]] = Counter()
    expected_passes: Counter[tuple[str, str]] = Counter()
    products: set[str] = set()
    for path in yield_paths:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            required = {"Product_Name", "Lot_ID", "Wafer_ID", "Total", "Pass"}
            if not required.issubset(reader.fieldnames or ()):
                raise CpCsvTripletError(f"CP yield columns are incomplete: {path.name}")
            for row in reader:
                lot_id = (row["Lot_ID"] or "").strip()
                wafer_id = _wafer_id((row["Wafer_ID"] or "").strip())
                product = (row["Product_Name"] or "").strip()
                if lot_id.upper() == "ALL" or wafer_id.upper() == "ALL":
                    continue
                if product:
                    products.add(product)
                key = (lot_id, wafer_id)
                expected_counts[key] += int(row["Total"])
                expected_passes[key] += int(row["Pass"])
    if len(products) != 1:
        raise CpCsvTripletError(
            "CP Route A currently requires one explicit Product per upload task"
        )
    actual_counts = Counter((row.lot_id, row.wafer_id) for row in cleaned_rows)
    actual_passes = Counter(
        (row.lot_id, row.wafer_id) for row in cleaned_rows if row.bin_value == "1"
    )
    if actual_counts != expected_counts:
        raise CpCsvTripletError("CP cleaned/yield Total reconciliation failed")
    if actual_passes != expected_passes:
        raise CpCsvTripletError("CP Bin=1/yield Pass reconciliation failed")
    return CpCsvTriplet(
        product_name=next(iter(products)),
        parameters=parameters,
        spec_items=declared_spec_items,
        rows=tuple(cleaned_rows),
        spec_sha256=hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        source_paths=tuple(str(path) for path in cleaned_paths + yield_paths + (spec_path,)),
        pass_count=sum(actual_passes.values()),
    )


class CpCsvTripletWriter:
    def __init__(self, engine: Engine, *, batch_size: int = 5000) -> None:
        self._engine = engine
        self._batch_size = max(batch_size, 1)

    @staticmethod
    def _scalar(connection: Connection, sql: str, parameters: dict[str, Any]) -> int:
        return int(connection.execute(text(sql), parameters).scalar_one())

    def write(
        self,
        *,
        job_id: int,
        import_batch_id: int,
        artifacts: tuple[CleanerArtifact, ...],
    ) -> CpCanonicalImportResult:
        triplet = parse_cp_csv_triplet(artifacts)
        measurement_count = len(triplet.rows) * len(triplet.parameters)
        with self._engine.begin() as connection:
            context = (
                connection.execute(
                    text(
                        "SELECT b.owner_user_id,b.business_domain,b.test_stage,b.factory_code,"
                        "j.status AS job_status,j.cleaner_release_id,cr.output_contract_version,"
                        "sf.source_file_id FROM ingestion.import_batch b "
                        "JOIN ingestion.processing_job j ON j.import_batch_id=b.import_batch_id "
                        "JOIN ingestion.cleaner_release cr ON cr.cleaner_release_id=j.cleaner_release_id "
                        "CROSS APPLY(SELECT TOP(1) sfr.source_file_id FROM ingestion.import_batch_file ibf "
                        "JOIN ingestion.source_file_receipt sfr ON sfr.receipt_id=ibf.receipt_id "
                        "WHERE ibf.import_batch_id=b.import_batch_id ORDER BY ibf.ordinal_no) sf "
                        "WHERE b.import_batch_id=:batch AND j.job_id=:job"
                    ),
                    {"batch": import_batch_id, "job": job_id},
                )
                .mappings()
                .one_or_none()
            )
            if context is None:
                raise CpCsvTripletError("CP import job/batch context was not found")
            if context["job_status"] != "RUNNING":
                raise CpCsvTripletError("CP import job must be RUNNING")
            if context["test_stage"] != "CP":
                raise CpCsvTripletError("CP writer received a non-CP upload task")
            if context["output_contract_version"] != "CP_CSV_TRIPLET_V1":
                raise CpCsvTripletError("CP Cleaner output contract is not supported")

            supplier_id = connection.execute(
                text("SELECT supplier_id FROM mdm.supplier WHERE supplier_code='HUAHONG'"),
            ).scalar_one_or_none()
            if supplier_id is None:
                supplier_id = self._scalar(
                    connection,
                    "INSERT mdm.supplier(supplier_code,supplier_name,supplier_type,active) "
                    "OUTPUT INSERTED.supplier_id VALUES('HUAHONG',N'华虹','WAFER_FAB',1)",
                    {},
                )
            product_id = connection.execute(
                text("SELECT product_id FROM mdm.product WHERE product_code=:code"),
                {"code": triplet.product_name},
            ).scalar_one_or_none()
            if product_id is None:
                product_id = self._scalar(
                    connection,
                    "INSERT mdm.product(product_code,product_name,active) OUTPUT INSERTED.product_id "
                    "VALUES(:code,:name,1)",
                    {"code": triplet.product_name, "name": triplet.product_name},
                )
            program_id = connection.execute(
                text(
                    "SELECT test_program_id FROM mdm.test_program WHERE supplier_id=:supplier "
                    "AND product_id=:product AND test_stage='CP' AND program_code='CP_CSV_TRIPLET_V1'"
                ),
                {"supplier": supplier_id, "product": product_id},
            ).scalar_one_or_none()
            if program_id is None:
                program_id = self._scalar(
                    connection,
                    "INSERT mdm.test_program(supplier_id,product_id,test_stage,program_code,program_name,active) "
                    "OUTPUT INSERTED.test_program_id VALUES(:supplier,:product,'CP','CP_CSV_TRIPLET_V1',"
                    "N'华虹 CP Cleaner 标准输出',1)",
                    {"supplier": supplier_id, "product": product_id},
                )
            version_code = f"SPEC-{triplet.spec_sha256[:16].upper()}-CONT-NONPARAM"
            program_version_id = connection.execute(
                text(
                    "SELECT program_version_id FROM mdm.test_program_version "
                    "WHERE test_program_id=:program AND version_code=:version"
                ),
                {"program": program_id, "version": version_code},
            ).scalar_one_or_none()
            if program_version_id is None:
                program_version_id = self._scalar(
                    connection,
                    "INSERT mdm.test_program_version(test_program_id,version_code,raw_program_name,program_checksum,metadata_json) "
                    "OUTPUT INSERTED.program_version_id VALUES(:program,:version,N'CP_CSV_TRIPLET_V1',:sha,:metadata)",
                    {
                        "program": program_id,
                        "version": version_code,
                        "sha": triplet.spec_sha256,
                        "metadata": json.dumps(
                            {
                                "first_batch_spec": True,
                                "non_parameter_columns": ["CONT"],
                            }
                        ),
                    },
                )
                connection.execute(
                    text(
                        "INSERT mdm.test_item_definition(program_version_id,sequence_no,step_code,raw_item_name,"
                        "canonical_parameter_code,display_name,data_type,unit_raw,unit_code,program_lsl,program_usl,"
                        "lower_operator,upper_operator,lower_limit_raw,upper_limit_raw,condition_json,"
                        "source_column_index,is_analysis_parameter) VALUES(:version,:sequence,:step,:name,:name,:name,"
                        "'NUMERIC',:unit,:unit,:lsl,:usl,'>=','<=',:raw_lsl,:raw_usl,:condition,:column_index,1)"
                    ),
                    [
                        {
                            "version": program_version_id,
                            "sequence": index,
                            "step": item.name,
                            "name": item.name,
                            "unit": item.unit,
                            "lsl": item.lsl,
                            "usl": item.usl,
                            "raw_lsl": item.raw_lsl,
                            "raw_usl": item.raw_usl,
                            "condition": json.dumps(
                                {"text": item.test_condition}, ensure_ascii=False
                            ),
                            "column_index": index + 5,
                        }
                        for index, item in enumerate(triplet.spec_items, start=1)
                    ],
                )
            item_rows = connection.execute(
                text(
                    "SELECT test_item_id,raw_item_name FROM mdm.test_item_definition "
                    "WHERE program_version_id=:version ORDER BY sequence_no"
                ),
                {"version": program_version_id},
            ).mappings().all()
            item_ids = {str(row["raw_item_name"]): int(row["test_item_id"]) for row in item_rows}
            if tuple(item_ids) != triplet.parameters:
                raise CpCsvTripletError("stored CP test items differ from the Cleaner Spec")

            parser_profile_id = connection.execute(
                text(
                    "SELECT parser_profile_id FROM ingestion.parser_profile "
                    "WHERE format_code='HUAHONG_CP_CSV_TRIPLET' AND parser_version='1.1'"
                )
            ).scalar_one_or_none()
            if parser_profile_id is None:
                connection.execute(
                    text(
                        "UPDATE ingestion.parser_profile SET is_default=0 "
                        "WHERE format_code='HUAHONG_CP_CSV_TRIPLET' AND is_default=1"
                    )
                )
                parser_profile_id = self._scalar(
                    connection,
                    "INSERT ingestion.parser_profile(format_code,supplier_id,test_stage,parser_name,parser_version,"
                    "canonical_model_version,detect_rules_json,active,is_default) OUTPUT INSERTED.parser_profile_id "
                    "VALUES('HUAHONG_CP_CSV_TRIPLET',:supplier,'CP','CP_CSV_TRIPLET_V1','1.1','1.0',"
                    "'{\"non_parameter_columns\":[\"CONT\"]}',1,1)",
                    {"supplier": supplier_id},
                )
            spec_set_id = connection.execute(
                text(
                    "SELECT spec_set_id FROM mdm.spec_set WHERE test_stage='CP' AND source_ref=:source"
                ),
                {"source": f"sha256:{triplet.spec_sha256}:CONT_NON_PARAMETER"},
            ).scalar_one_or_none()
            if spec_set_id is None:
                spec_set_id = self._scalar(
                    connection,
                    "INSERT mdm.spec_set(product_id,test_stage,spec_name,version_code,status,source_type,source_ref,metadata_json) "
                    "OUTPUT INSERTED.spec_set_id VALUES(:product,'CP',:name,:version,'RELEASED','CLEANER_OUTPUT',:source,:metadata)",
                    {
                        "product": product_id,
                        "name": f"{triplet.product_name} first-batch Spec",
                        "version": version_code,
                        "source": f"sha256:{triplet.spec_sha256}:CONT_NON_PARAMETER",
                        "metadata": json.dumps(
                            {
                                "selection_rule": "FIRST_BATCH",
                                "non_parameter_columns": ["CONT"],
                            }
                        ),
                    },
                )
                connection.execute(
                    text(
                        "INSERT mdm.spec_item(spec_set_id,test_item_id,canonical_parameter_code,lsl,usl,"
                        "lower_operator,upper_operator,unit_code,raw_spec,condition_json) "
                        "VALUES(:spec,:item,:name,:lsl,:usl,'>=','<=',:unit,:raw_spec,:condition)"
                    ),
                    [
                        {
                            "spec": spec_set_id,
                            "item": item_ids[item.name],
                            "name": item.name,
                            "lsl": item.lsl,
                            "usl": item.usl,
                            "unit": item.unit,
                            "raw_spec": json.dumps(
                                {"lsl": item.raw_lsl, "usl": item.raw_usl},
                                ensure_ascii=False,
                            ),
                            "condition": json.dumps(
                                {"text": item.test_condition}, ensure_ascii=False
                            ),
                        }
                        for item in triplet.spec_items
                    ],
                )

            processing_run_id = self._scalar(
                connection,
                "INSERT ingestion.processing_run(job_id,source_file_id,parser_profile_id,parser_version,"
                "canonical_model_version,status,is_current,row_count_input,unit_count_output,measurement_count_output,"
                "started_at_utc,finished_at_utc,metadata_json) OUTPUT INSERTED.processing_run_id "
                "VALUES(:job,:source,:parser,'1.1','1.0','READY',0,:units,:units,:measurements,"
                "SYSUTCDATETIME(),SYSUTCDATETIME(),:metadata)",
                {
                    "job": job_id,
                    "source": context["source_file_id"],
                    "parser": parser_profile_id,
                    "units": len(triplet.rows),
                    "measurements": measurement_count,
                    "metadata": json.dumps(
                        {
                            "output_contract": "CP_CSV_TRIPLET_V1",
                            "business_domain": context["business_domain"],
                            "source_paths": triplet.source_paths,
                            "spec_set_id": spec_set_id,
                        },
                        ensure_ascii=False,
                    ),
                },
            )
            run_ids: dict[tuple[str, str], int] = {}
            raw_wafer_ids = {
                (row.lot_id, row.wafer_id): row.raw_wafer_id for row in triplet.rows
            }
            for lot_id, wafer_id in sorted({(row.lot_id, row.wafer_id) for row in triplet.rows}):
                run_ids[(lot_id, wafer_id)] = self._scalar(
                    connection,
                    "INSERT test.test_run(processing_run_id,supplier_id,product_id,program_version_id,test_stage,"
                    "lot_id,wafer_id,run_attempt_no,timezone_resolution,timestamp_source,metadata_json) "
                    "OUTPUT INSERTED.run_id VALUES(:processing,:supplier,:product,:program,'CP',:lot,:wafer,0,"
                    "'UNKNOWN','UNKNOWN',:metadata)",
                    {
                        "processing": processing_run_id,
                        "supplier": supplier_id,
                        "product": product_id,
                        "program": program_version_id,
                        "lot": lot_id,
                        "wafer": wafer_id,
                        "metadata": json.dumps(
                            {
                                "spec_set_id": spec_set_id,
                                "raw_wafer_id": raw_wafer_ids[(lot_id, wafer_id)],
                            }
                        ),
                    },
                )
            unit_parameters = [
                {
                    "run": run_ids[(row.lot_id, row.wafer_id)],
                    "key": row.logical_key,
                    "seq": row.seq_no,
                    "wafer": row.wafer_id,
                    "x": row.x,
                    "y": row.y,
                    "bin": row.bin_value,
                    "result": "PASS" if row.bin_value == "1" else "FAIL",
                    "source_row": row.source_row_no,
                    "metadata": json.dumps({"raw_lot_id": row.lot_id}),
                }
                for row in triplet.rows
            ]
            insert_unit = text(
                "INSERT test.unit_result(run_id,logical_unit_key,attempt_no,unit_sequence,wafer_id,x_coord,y_coord,"
                "soft_bin,overall_result,source_row_no,metadata_json) VALUES(:run,:key,0,:seq,:wafer,:x,:y,:bin,"
                ":result,:source_row,:metadata)"
            )
            for start in range(0, len(unit_parameters), self._batch_size):
                connection.execute(insert_unit, unit_parameters[start : start + self._batch_size])
            unit_rows = connection.execute(
                text(
                    "SELECT ur.logical_unit_key,ur.unit_id FROM test.unit_result ur "
                    "JOIN test.test_run tr ON tr.run_id=ur.run_id "
                    "WHERE tr.processing_run_id=:processing"
                ),
                {"processing": processing_run_id},
            ).mappings().all()
            unit_ids = {str(row["logical_unit_key"]): int(row["unit_id"]) for row in unit_rows}
            if len(unit_ids) != len(triplet.rows):
                raise CpCsvTripletError("CP unit identity reconciliation failed after insert")
            measurement_parameters: list[dict[str, Any]] = []
            insert_measurement = text(
                "INSERT test.measurement(unit_id,test_item_id,value_numeric,value_text,raw_value,measurement_status,"
                "tester_pass_flag,source_column_index) VALUES(:unit,:item,:numeric,NULL,:raw,:status,NULL,:column_index)"
            )
            for row in triplet.rows:
                for index, (name, raw) in enumerate(
                    zip(triplet.parameters, row.values, strict=True), start=1
                ):
                    measurement_parameters.append(
                        {
                            "unit": unit_ids[row.logical_key],
                            "item": item_ids[name],
                            "numeric": _number(
                                raw,
                                field=f"row {row.source_row_no} {name}",
                                allow_blank=True,
                            ),
                            "raw": raw or None,
                            "status": "MEASURED" if raw else "MISSING",
                            "column_index": index + 5,
                        }
                    )
                    if len(measurement_parameters) >= self._batch_size:
                        connection.execute(insert_measurement, measurement_parameters)
                        measurement_parameters.clear()
            if measurement_parameters:
                connection.execute(insert_measurement, measurement_parameters)

            dataset_code = f"CP-BATCH-{import_batch_id}"
            dataset_id = connection.execute(
                text("SELECT dataset_id FROM dataset.dataset WHERE dataset_code=:code"),
                {"code": dataset_code},
            ).scalar_one_or_none()
            lots = sorted({row.lot_id for row in triplet.rows})
            if dataset_id is None:
                dataset_id = self._scalar(
                    connection,
                    "INSERT dataset.dataset(dataset_code,dataset_name,dataset_type,test_stage,supplier_id,product_id,"
                    "owner_user_id) OUTPUT INSERTED.dataset_id VALUES(:code,:name,'CP_DETAIL','CP',:supplier,:product,:owner)",
                    {
                        "code": dataset_code,
                        "name": f"{context['business_domain']} CP {'/'.join(lots)}",
                        "supplier": supplier_id,
                        "product": product_id,
                        "owner": context["owner_user_id"],
                    },
                )
            previous = connection.execute(
                text(
                    "SELECT dataset_version_id FROM dataset.dataset_version WITH (UPDLOCK,HOLDLOCK) "
                    "WHERE dataset_id=:dataset AND status='PUBLISHED' AND is_current=1"
                ),
                {"dataset": dataset_id},
            ).scalar_one_or_none()
            version_no = int(
                connection.execute(
                    text(
                        "SELECT ISNULL(MAX(version_no),0)+1 FROM dataset.dataset_version WITH (UPDLOCK,HOLDLOCK) "
                        "WHERE dataset_id=:dataset"
                    ),
                    {"dataset": dataset_id},
                ).scalar_one()
            )
            if previous is not None:
                connection.execute(
                    text(
                        "UPDATE dataset.dataset_version SET status='SUPERSEDED',is_current=0 "
                        "WHERE dataset_version_id=:previous"
                    ),
                    {"previous": previous},
                )
            version_id = self._scalar(
                connection,
                "INSERT dataset.dataset_version(dataset_id,version_no,input_batch_id,canonical_model_version,status,"
                "is_current,row_count,unit_count,measurement_count,published_by,published_at_utc,"
                "supersedes_dataset_version_id,spec_set_id,metadata_json) OUTPUT INSERTED.dataset_version_id "
                "VALUES(:dataset,:version,:batch,'1.0','PUBLISHED',1,:units,:units,:measurements,:owner,"
                "SYSUTCDATETIME(),:previous,:spec,:metadata)",
                {
                    "dataset": dataset_id,
                    "version": version_no,
                    "batch": import_batch_id,
                    "units": len(triplet.rows),
                    "measurements": measurement_count,
                    "owner": context["owner_user_id"],
                    "previous": previous,
                    "spec": spec_set_id,
                    "metadata": json.dumps(
                        {"spec_selection_rule": "FIRST_BATCH"}, ensure_ascii=False
                    ),
                },
            )
            connection.execute(
                text(
                    "INSERT dataset.dataset_version_run(dataset_version_id,processing_run_id,run_role,ordinal_no) "
                    "VALUES(:version,:processing,'PRIMARY',1)"
                ),
                {"version": version_id, "processing": processing_run_id},
            )
            connection.execute(
                text(
                    "UPDATE ingestion.processing_run SET status='PUBLISHED',is_current=0 "
                    "WHERE processing_run_id=:processing"
                ),
                {"processing": processing_run_id},
            )
            connection.execute(
                text(
                    "UPDATE ingestion.processing_artifact SET processing_run_id=:processing "
                    "WHERE job_id=:job"
                ),
                {"processing": processing_run_id, "job": job_id},
            )
        return CpCanonicalImportResult(
            processing_run_id=processing_run_id,
            dataset_id=int(dataset_id),
            dataset_version_no=version_no,
            spec_set_id=int(spec_set_id),
            unit_count=len(triplet.rows),
            measurement_count=measurement_count,
        )
