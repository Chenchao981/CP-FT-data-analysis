from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from app.infrastructure.existing_cleaner_runner import CleanerArtifact
from app.infrastructure.initial_import_staging import (
    insert_draft_dataset_version,
    prepare_atomic_stage,
    record_atomic_stage,
)
from app.infrastructure.sql_master_data_service import observe_product_crosswalk

CP_MULTI_LOT_SPEC_BINDING_REQUIRED = "CP_MULTI_LOT_SPEC_BINDING_REQUIRED"


class CpCsvTripletError(ValueError):
    pass


class CpMultiLotSpecBindingRequired(CpCsvTripletError):
    error_code = CP_MULTI_LOT_SPEC_BINDING_REQUIRED


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
        return f"CP:{self.lot_id}:{self.wafer_id}:{self.x}:{self.y}:{self.seq_no}"


@dataclass(frozen=True, slots=True)
class CpCsvTriplet:
    product_name: str | None
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
    dataset_version_id: int
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


CP_PROCESS_COLUMNS = {"CONT", "SITE_NUM", "T_TIME", "TEST_NUM"}


def _parameter_contract_sha256(triplet: CpCsvTriplet) -> str:
    """Fingerprint the canonical parameter contract, not only the raw Spec file."""

    payload = {
        "schema_version": "CP_PARAMETER_CONTRACT_V1",
        "non_parameter_columns": sorted(CP_PROCESS_COLUMNS),
        "parameters": [
            {
                "name": item.name,
                "unit": item.unit,
                "raw_lsl": item.raw_lsl,
                "raw_usl": item.raw_usl,
                "test_condition": item.test_condition,
            }
            for item in triplet.spec_items
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stored_parameter_contract_matches(
    rows: list[dict[str, Any]] | list[Any], triplet: CpCsvTriplet
) -> bool:
    if len(rows) != len(triplet.spec_items):
        return False

    def normalized(value: object) -> str | None:
        if value is None:
            return None
        text_value = str(value).strip()
        return text_value or None

    for index, (row, item) in enumerate(zip(rows, triplet.spec_items, strict=True), start=1):
        condition_text = None
        try:
            condition = json.loads(str(row["condition_json"] or "{}"))
            condition_text = normalized(condition.get("text"))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return False
        if (
            int(row["sequence_no"]) != index
            or str(row["raw_item_name"]) != item.name
            or str(row["canonical_parameter_code"] or "") != item.name
            or normalized(row["unit_code"]) != normalized(item.unit)
            or normalized(row["lower_limit_raw"]) != normalized(item.raw_lsl)
            or normalized(row["upper_limit_raw"]) != normalized(item.raw_usl)
            or condition_text != normalized(item.test_condition)
            or not bool(row["is_analysis_parameter"])
        ):
            return False
    return True


def _read_spec(path: Path) -> tuple[tuple[str, ...], tuple[CpSpecItem, ...]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    if not rows or not rows[0] or rows[0][0].strip() != "Parameter":
        raise CpCsvTripletError("CP spec CSV has an unsupported header")
    if len(rows[0]) >= 4 and rows[0][1].strip().upper() == "UNIT":
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = set(reader.fieldnames or ())
            if not {"Parameter", "Unit", "LimitL", "LimitU"}.issubset(columns):
                raise CpCsvTripletError("CP row spec CSV is incomplete")
            items = []
            for row in reader:
                name = (row.get("Parameter") or "").strip()
                if not name or name in CP_PROCESS_COLUMNS:
                    continue
                raw_lsl = (row.get("LimitL") or row.get("LSL") or "").strip() or None
                raw_usl = (row.get("LimitU") or row.get("USL") or "").strip() or None
                items.append(
                    CpSpecItem(
                        name=name,
                        unit=(row.get("Unit") or "").strip() or None,
                        lsl=_number(raw_lsl or "", field=f"{name} LSL", allow_blank=True),
                        usl=_number(raw_usl or "", field=f"{name} USL", allow_blank=True),
                        raw_lsl=raw_lsl,
                        raw_usl=raw_usl,
                        test_condition=(row.get("Test_Condition") or "").strip() or None,
                    )
                )
        if not items or len({item.name for item in items}) != len(items):
            raise CpCsvTripletError("CP row spec parameters are empty or duplicated")
        return tuple(item.name for item in items), tuple(items)
    if len(rows) < 4:
        raise CpCsvTripletError("CP matrix spec CSV is incomplete")
    parameters = tuple(value.strip() for value in rows[0][1:])
    if not parameters or any(not value for value in parameters):
        raise CpCsvTripletError("CP spec CSV contains a blank parameter")
    if len(set(parameters)) != len(parameters):
        raise CpCsvTripletError("CP spec CSV contains duplicate parameters")
    label_aliases = {
        "UNIT": "Unit",
        "LIMITU": "LimitU",
        "LIMITL": "LimitL",
        "LIMITHIGH": "LimitU",
        "LIMITLOW": "LimitL",
    }
    indexed = {
        label_aliases.get(row[0].strip().upper().replace("_", ""), row[0].strip()): row[1:]
        for row in rows[1:4]
        if row
    }
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
    parameter_items = tuple(item for item in items if item.name not in CP_PROCESS_COLUMNS)
    return tuple(item.name for item in parameter_items), parameter_items


def parse_cp_csv_triplet(
    artifacts: tuple[CleanerArtifact, ...],
) -> CpCsvTriplet:
    cleaned_paths = _artifact_paths(artifacts, "cleaned")
    yield_paths = _artifact_paths(artifacts, "yield")
    spec_path = _artifact_paths(artifacts, "spec")[0]
    spec_parameters, declared_spec_items = _read_spec(spec_path)
    identity_aliases = {
        "Lot_ID": ("Lot_ID", "LotID"),
        "Wafer_ID": ("Wafer_ID", "WaferID"),
        "Seq": ("Seq",),
        "Bin": ("Bin",),
        "X": ("X",),
        "Y": ("Y",),
    }
    parameters: tuple[str, ...] | None = None
    source_parameter_columns: tuple[str, ...] | None = None
    cleaned_rows: list[CpCleanedRow] = []
    seen_keys: set[str] = set()
    for path in cleaned_paths:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = tuple(reader.fieldnames or ())
            resolved = {
                target: next((name for name in aliases if name in columns), None)
                for target, aliases in identity_aliases.items()
            }
            if any(name is None for name in resolved.values()):
                raise CpCsvTripletError(
                    f"CP cleaned identity columns are unsupported: {path.name}"
                )
            identity_columns = {str(name) for name in resolved.values()}
            measured_columns = tuple(
                name
                for name in columns
                if name not in identity_columns and name not in CP_PROCESS_COLUMNS
            )
            if not measured_columns or len(set(measured_columns)) != len(
                measured_columns
            ):
                raise CpCsvTripletError(
                    f"CP cleaned measurement columns are invalid: {path.name}"
                )
            if parameters is None:
                source_parameter_columns = measured_columns
                parameters = measured_columns
                if parameters != spec_parameters:
                    raise CpCsvTripletError(
                        "CP cleaned parameters do not match first Spec after excluding CONT"
                    )
            if measured_columns != source_parameter_columns:
                raise CpCsvTripletError(
                    f"CP cleaned measurement columns differ: {path.name}"
                )
            for source_row_no, row in enumerate(reader, start=2):
                lot_id = (row[str(resolved["Lot_ID"])] or "").strip()
                raw_wafer_id = (row[str(resolved["Wafer_ID"])] or "").strip()
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
                        seq_no=int(Decimal(row[str(resolved["Seq"])])),
                        bin_value=str(int(Decimal(row[str(resolved["Bin"])]))),
                        x=int(Decimal(row[str(resolved["X"])])),
                        y=int(Decimal(row[str(resolved["Y"])])),
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
            columns = set(reader.fieldnames or ())
            required = {"Lot_ID", "Wafer_ID"}
            total_name = "Total" if "Total" in columns else "Gross_die"
            pass_name = "Pass" if "Pass" in columns else "Good_die"
            if not required.issubset(columns) or total_name not in columns or pass_name not in columns:
                raise CpCsvTripletError(f"CP yield columns are incomplete: {path.name}")
            for row in reader:
                lot_id = (row["Lot_ID"] or "").strip()
                wafer_id = _wafer_id((row["Wafer_ID"] or "").strip())
                product = (row.get("Product_Name") or "").strip()
                if lot_id.upper() == "ALL" or wafer_id.upper() == "ALL":
                    continue
                if product:
                    products.add(product)
                key = (lot_id, wafer_id)
                expected_counts[key] += int(float(row[total_name]))
                expected_passes[key] += int(float(row[pass_name]))
    lot_ids = sorted(
        {row.lot_id for row in cleaned_rows}
        | {
            lot_id
            for lot_id, _wafer_id_value in expected_counts
            if lot_id
        },
        key=lambda value: (value.casefold(), value),
    )
    if len(lot_ids) > 1:
        lot_preview = ", ".join(lot_ids[:10])
        if len(lot_ids) > 10:
            lot_preview = f"{lot_preview}, ..."
        raise CpMultiLotSpecBindingRequired(
            f"{CP_MULTI_LOT_SPEC_BINDING_REQUIRED}: CP CSV triplet V1 has no "
            f"explicit per-Lot Spec binding; found {len(lot_ids)} Lots: {lot_preview}"
        )
    if len(products) > 1:
        raise CpCsvTripletError(
            "CP Route A requires at most one explicit Product per upload task"
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
        product_name=next(iter(products)) if products else None,
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
        lease_token: str,
        artifacts: tuple[CleanerArtifact, ...],
        finalize_summary: Mapping[str, Any],
    ) -> CpCanonicalImportResult:
        triplet = parse_cp_csv_triplet(artifacts)
        measurement_count = len(triplet.rows) * len(triplet.parameters)
        single_lot_id = triplet.rows[0].lot_id
        with self._engine.begin() as connection:
            preparation = prepare_atomic_stage(
                connection,
                job_id=job_id,
                import_batch_id=import_batch_id,
                lease_token=lease_token,
                artifacts=artifacts,
            )
            context = preparation.context
            if context["test_stage"] != "CP":
                raise CpCsvTripletError("CP writer received a non-CP upload task")
            if context["output_contract_version"] not in {
                "CP_CSV_TRIPLET_V1",
                "CP_STANDARD_CSV_TRIPLET_V1",
            }:
                raise CpCsvTripletError("CP Cleaner output contract is not supported")
            if preparation.existing is not None:
                existing = preparation.existing
                if existing.spec_set_id is None:
                    raise CpCsvTripletError(
                        "CP staged Dataset Version has no explicit Spec binding"
                    )
                return CpCanonicalImportResult(
                    processing_run_id=existing.processing_run_id,
                    dataset_id=existing.dataset_id,
                    dataset_version_id=existing.dataset_version_id,
                    dataset_version_no=existing.dataset_version_no,
                    spec_set_id=existing.spec_set_id,
                    unit_count=existing.unit_count,
                    measurement_count=existing.measurement_count,
                )

            supplier_code = str(context["factory_code"]).strip().upper()
            supplier_names = {
                "HUAHONG": "华虹",
                "JETECH": "Jetech",
                "LION": "立昂微",
                "GUOYU": "国宇FRD",
            }
            supplier_name = supplier_names.get(supplier_code, supplier_code)
            supplier_id = connection.execute(
                text("SELECT supplier_id FROM mdm.supplier WHERE supplier_code=:code"),
                {"code": supplier_code},
            ).scalar_one_or_none()
            if supplier_id is None:
                supplier_id = self._scalar(
                    connection,
                    "INSERT mdm.supplier(supplier_code,supplier_name,supplier_type,active) "
                    "OUTPUT INSERTED.supplier_id VALUES(:code,:name,'WAFER_FAB',1)",
                    {"code": supplier_code, "name": supplier_name},
                )
            product_id = None
            if triplet.product_name:
                product_id = connection.execute(
                    text("SELECT product_id FROM mdm.product WHERE product_code=:code"),
                    {"code": triplet.product_name, "name": triplet.product_name},
                ).scalar_one_or_none()
                if product_id is None:
                    product_id = self._scalar(
                        connection,
                        "INSERT mdm.product(product_code,product_name,active) OUTPUT INSERTED.product_id "
                        "VALUES(:code,:name,1)",
                        {"code": triplet.product_name, "name": triplet.product_name},
                    )
            if product_id is not None and triplet.product_name:
                observe_product_crosswalk(
                    connection,
                    supplier_id=int(supplier_id),
                    product_id=int(product_id),
                    test_stage="CP",
                    raw_product_code=triplet.product_name,
                )
            program_code = str(context["output_contract_version"])
            program_id = connection.execute(
                text(
                    "SELECT test_program_id FROM mdm.test_program WHERE supplier_id=:supplier "
                    "AND ((product_id=:product) OR (product_id IS NULL AND :product IS NULL)) "
                    "AND test_stage='CP' AND program_code=:program"
                ),
                {"supplier": supplier_id, "product": product_id, "program": program_code},
            ).scalar_one_or_none()
            if program_id is None:
                program_id = self._scalar(
                    connection,
                    "INSERT mdm.test_program(supplier_id,product_id,test_stage,program_code,program_name,active) "
                    "OUTPUT INSERTED.test_program_id VALUES(:supplier,:product,'CP',:program,:name,1)",
                    {
                        "supplier": supplier_id,
                        "product": product_id,
                        "program": program_code,
                        "name": f"{supplier_name} CP Cleaner 标准输出",
                    },
                )
            parameter_contract_sha256 = _parameter_contract_sha256(triplet)
            compatible_version = None
            version_candidates = (
                connection.execute(
                    text(
                        "SELECT program_version_id,version_code "
                        "FROM mdm.test_program_version WITH (UPDLOCK,HOLDLOCK) "
                        "WHERE test_program_id=:program AND program_checksum=:checksum "
                        "ORDER BY program_version_id"
                    ),
                    {"program": program_id, "checksum": triplet.spec_sha256},
                )
                .mappings()
                .all()
            )
            for candidate in version_candidates:
                candidate_items = (
                    connection.execute(
                        text(
                            "SELECT sequence_no,raw_item_name,canonical_parameter_code,"
                            "unit_code,lower_limit_raw,upper_limit_raw,condition_json,"
                            "is_analysis_parameter "
                            "FROM mdm.test_item_definition WITH (HOLDLOCK) "
                            "WHERE program_version_id=:version ORDER BY sequence_no"
                        ),
                        {"version": int(candidate["program_version_id"])},
                    )
                    .mappings()
                    .all()
                )
                if _stored_parameter_contract_matches(candidate_items, triplet):
                    compatible_version = candidate
                    break

            version_code = (
                str(compatible_version["version_code"])
                if compatible_version is not None
                else (
                    f"SPEC-{triplet.spec_sha256[:16].upper()}-"
                    f"PARAM-{parameter_contract_sha256[:12].upper()}"
                )
            )
            program_version_id = (
                int(compatible_version["program_version_id"])
                if compatible_version is not None
                else connection.execute(
                    text(
                        "SELECT program_version_id FROM mdm.test_program_version "
                        "WITH (UPDLOCK,HOLDLOCK) "
                        "WHERE test_program_id=:program AND version_code=:version"
                    ),
                    {"program": program_id, "version": version_code},
                ).scalar_one_or_none()
            )
            if program_version_id is None:
                program_version_id = self._scalar(
                    connection,
                    "INSERT mdm.test_program_version(test_program_id,version_code,raw_program_name,program_checksum,metadata_json) "
                    "OUTPUT INSERTED.program_version_id VALUES(:program,:version,:raw_program,:sha,:metadata)",
                    {
                        "program": program_id,
                        "version": version_code,
                        "raw_program": program_code,
                        "sha": triplet.spec_sha256,
                        "metadata": json.dumps(
                            {
                                "spec_binding_contract": "SINGLE_LOT_EXPLICIT_SPEC",
                                "binding_scope": "DATASET_VERSION",
                                "spec_sha256": triplet.spec_sha256,
                                "parameter_contract_sha256": parameter_contract_sha256,
                                "non_parameter_columns": sorted(CP_PROCESS_COLUMNS),
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
                            "column_index": index + 6,
                        }
                        for index, item in enumerate(triplet.spec_items, start=1)
                    ],
                )
            item_rows = connection.execute(
                text(
                    "SELECT test_item_id,sequence_no,raw_item_name,canonical_parameter_code,"
                    "unit_code,lower_limit_raw,upper_limit_raw,condition_json,"
                    "is_analysis_parameter FROM mdm.test_item_definition "
                    "WHERE program_version_id=:version ORDER BY sequence_no"
                ),
                {"version": program_version_id},
            ).mappings().all()
            if not _stored_parameter_contract_matches(item_rows, triplet):
                raise CpCsvTripletError(
                    "stored CP test items differ from the canonical parameter contract"
                )
            item_ids = {str(row["raw_item_name"]): int(row["test_item_id"]) for row in item_rows}

            parser_format = f"{supplier_code}_CP_CSV_TRIPLET"
            parser_version = "1.1" if supplier_code == "HUAHONG" else "1.0"
            parser_profile_id = connection.execute(
                text(
                    "SELECT parser_profile_id FROM ingestion.parser_profile "
                    "WHERE format_code=:format AND parser_version=:version"
                ),
                {"format": parser_format, "version": parser_version},
            ).scalar_one_or_none()
            if parser_profile_id is None:
                connection.execute(
                    text(
                        "UPDATE ingestion.parser_profile SET is_default=0 "
                        "WHERE format_code=:format AND is_default=1"
                    ),
                    {"format": parser_format},
                )
                parser_profile_id = self._scalar(
                    connection,
                    "INSERT ingestion.parser_profile(format_code,supplier_id,test_stage,parser_name,parser_version,"
                    "canonical_model_version,detect_rules_json,active,is_default) OUTPUT INSERTED.parser_profile_id "
                    "VALUES(:format,:supplier,'CP',:parser,:version,'1.0',:rules,1,1)",
                    {
                        "format": parser_format,
                        "supplier": supplier_id,
                        "parser": program_code,
                        "version": parser_version,
                        "rules": json.dumps({"non_parameter_columns": sorted(CP_PROCESS_COLUMNS)}),
                    },
                )
            spec_source_ref = (
                f"sha256:{triplet.spec_sha256}:{supplier_code}:"
                f"PARAM:{parameter_contract_sha256[:16]}"
            )
            spec_set_id = connection.execute(
                text(
                    "SELECT spec_set_id FROM mdm.spec_set WHERE test_stage='CP' "
                    "AND source_ref=:source "
                    "AND ((product_id=:product) OR (product_id IS NULL AND :product IS NULL))"
                ),
                {"source": spec_source_ref, "product": product_id},
            ).scalar_one_or_none()
            if spec_set_id is None:
                spec_set_id = self._scalar(
                    connection,
                    "INSERT mdm.spec_set(product_id,test_stage,spec_name,version_code,status,source_type,source_ref,metadata_json) "
                    "OUTPUT INSERTED.spec_set_id VALUES(:product,'CP',:name,:version,'RELEASED','CLEANER_OUTPUT',:source,:metadata)",
                    {
                        "product": product_id,
                        "name": (
                            f"{triplet.product_name or supplier_name} "
                            "single-Lot explicit Spec"
                        ),
                        "version": version_code,
                        "source": spec_source_ref,
                        "metadata": json.dumps(
                            {
                                "selection_rule": "SINGLE_LOT_EXPLICIT_SPEC",
                                "binding_scope": "DATASET_VERSION",
                                "spec_sha256": triplet.spec_sha256,
                                "parameter_contract_sha256": parameter_contract_sha256,
                                "non_parameter_columns": sorted(CP_PROCESS_COLUMNS),
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
                "VALUES(:job,:source,:parser,:parser_version,'1.0','READY',0,:units,:units,:measurements,"
                "SYSUTCDATETIME(),SYSUTCDATETIME(),:metadata)",
                {
                    "job": job_id,
                    "source": preparation.source_file_id,
                    "parser": parser_profile_id,
                    "parser_version": parser_version,
                    "units": len(triplet.rows),
                    "measurements": measurement_count,
                    "metadata": json.dumps(
                        {
                            "output_contract": program_code,
                            "business_domain": context["business_domain"],
                            "source_paths": triplet.source_paths,
                            "spec_set_id": spec_set_id,
                            "atomic_finalize_summary": dict(finalize_summary),
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
                business_lot_id = None if supplier_code == "GUOYU" else lot_id
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
                        "lot": business_lot_id,
                        "wafer": wafer_id,
                        "metadata": json.dumps(
                            {
                                "spec_set_id": spec_set_id,
                                "raw_wafer_id": raw_wafer_ids[(lot_id, wafer_id)],
                                "source_group": lot_id,
                                "business_lot_available": business_lot_id is not None,
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
            version_id, version_no = insert_draft_dataset_version(
                connection,
                dataset_id=int(dataset_id),
                import_batch_id=import_batch_id,
                unit_count=len(triplet.rows),
                measurement_count=measurement_count,
                spec_set_id=int(spec_set_id),
                metadata_json=json.dumps(
                    {
                        "spec_selection_rule": "SINGLE_LOT_EXPLICIT_SPEC",
                        "lot_id": single_lot_id,
                        "spec_sha256": triplet.spec_sha256,
                        "parameter_contract_sha256": parameter_contract_sha256,
                    },
                    ensure_ascii=False,
                ),
            )
            record_atomic_stage(
                connection,
                job_id=job_id,
                import_batch_id=import_batch_id,
                processing_run_id=processing_run_id,
                dataset_version_id=version_id,
                preparation=preparation,
            )
        return CpCanonicalImportResult(
            processing_run_id=processing_run_id,
            dataset_id=int(dataset_id),
            dataset_version_id=version_id,
            dataset_version_no=version_no,
            spec_set_id=int(spec_set_id),
            unit_count=len(triplet.rows),
            measurement_count=measurement_count,
        )
