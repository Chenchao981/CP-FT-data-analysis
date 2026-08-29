from __future__ import annotations

import csv
import hashlib
import json
import math
import unicodedata
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
    spec_fingerprint_sha256: str
    spec_source_sha256s: tuple[str, ...]
    source_paths: tuple[str, ...]
    lot_ids: tuple[str, ...]
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
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise CpCsvTripletError(f"{field} is not numeric: {raw!r}") from exc
    if not decimal_value.is_finite():
        raise CpCsvTripletError(f"{field} must be finite: {raw!r}")
    numeric_value = float(decimal_value)
    if not math.isfinite(numeric_value):
        raise CpCsvTripletError(f"{field} is outside the supported numeric range")
    return numeric_value


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


def _normalized_contract_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", str(value))
    normalized = " ".join(normalized.split())
    if not normalized:
        return None
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        raise CpCsvTripletError(f"{field} contains a control character")
    return normalized


def _normalized_parameter_name(value: object, *, field: str) -> str:
    normalized = _normalized_contract_text(value, field=field)
    if normalized is None:
        raise CpCsvTripletError(f"{field} is blank")
    return normalized


def _normalized_decimal(raw: object, *, field: str) -> str | None:
    normalized = _normalized_contract_text(raw, field=field)
    if normalized is None:
        return None
    try:
        number = Decimal(normalized)
    except InvalidOperation as exc:
        raise CpCsvTripletError(f"{field} is not numeric: {raw!r}") from exc
    if not number.is_finite():
        raise CpCsvTripletError(f"{field} must be finite: {raw!r}")
    if number == 0:
        return "0"
    canonical = format(number, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    return canonical


def _normalized_spec_entries(
    items: tuple[CpSpecItem, ...],
) -> tuple[dict[str, str | None], ...]:
    entries: list[dict[str, str | None]] = []
    seen_names: set[str] = set()
    for item in items:
        name = _normalized_parameter_name(item.name, field="CP Spec parameter")
        if name in seen_names:
            raise CpCsvTripletError(
                f"CP Spec has an ambiguous normalized parameter: {name}"
            )
        seen_names.add(name)
        entries.append(
            {
                "name": name,
                "unit": _normalized_contract_text(
                    item.unit, field=f"CP Spec {name} unit"
                ),
                "lsl": _normalized_decimal(
                    item.raw_lsl, field=f"CP Spec {name} LSL"
                ),
                "usl": _normalized_decimal(
                    item.raw_usl, field=f"CP Spec {name} USL"
                ),
                "test_condition": _normalized_contract_text(
                    item.test_condition, field=f"CP Spec {name} test condition"
                ),
            }
        )
    return tuple(sorted(entries, key=lambda entry: str(entry["name"])))


def _spec_fingerprint_sha256(items: tuple[CpSpecItem, ...]) -> str:
    payload = {
        "schema_version": "CP_NORMALIZED_SPEC_V1",
        "parameters": _normalized_spec_entries(items),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parameter_columns_by_normalized_name(
    columns: tuple[str, ...], *, source: str
) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for column in columns:
        normalized = _normalized_parameter_name(
            column, field=f"CP parameter column in {source}"
        )
        if normalized in mapped:
            raise CpCsvTripletError(
                f"CP parameter columns are ambiguous after normalization: {source}"
            )
        mapped[normalized] = column
    return mapped


def _lot_binding_key(value: object, *, field: str) -> str:
    return _normalized_parameter_name(value, field=field).casefold()


def _spec_filename_lot_key(path: Path) -> str | None:
    marker_index = path.name.casefold().rfind("_spec_")
    if marker_index <= 0:
        return None
    prefix = path.name[:marker_index]
    try:
        return _lot_binding_key(prefix, field=f"CP Spec filename {path.name}")
    except CpCsvTripletError:
        return None


def _parameter_contract_sha256(triplet: CpCsvTriplet) -> str:
    """Fingerprint the normalized canonical parameter contract."""

    payload = {
        "schema_version": "CP_PARAMETER_CONTRACT_V2",
        "non_parameter_columns": sorted(CP_PROCESS_COLUMNS),
        "parameters": _normalized_spec_entries(triplet.spec_items),
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
    stored_items: list[CpSpecItem] = []
    sequences: set[int] = set()
    for row in rows:
        try:
            condition = json.loads(str(row["condition_json"] or "{}"))
            condition_text = condition.get("text")
            sequence = int(row["sequence_no"])
            raw_name = _normalized_parameter_name(
                row["raw_item_name"], field="stored CP raw item name"
            )
            canonical_name = _normalized_parameter_name(
                row["canonical_parameter_code"],
                field="stored CP canonical parameter code",
            )
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            CpCsvTripletError,
        ):
            return False
        if sequence < 1 or sequence in sequences or raw_name != canonical_name:
            return False
        if not bool(row["is_analysis_parameter"]):
            return False
        sequences.add(sequence)
        stored_items.append(
            CpSpecItem(
                name=raw_name,
                unit=row["unit_code"],
                lsl=None,
                usl=None,
                raw_lsl=row["lower_limit_raw"],
                raw_usl=row["upper_limit_raw"],
                test_condition=condition_text,
            )
        )
    if sequences != set(range(1, len(rows) + 1)):
        return False
    try:
        return _normalized_spec_entries(tuple(stored_items)) == _normalized_spec_entries(
            triplet.spec_items
        )
    except CpCsvTripletError:
        return False


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
    spec_paths = _artifact_paths(artifacts, "spec")
    parsed_specs: list[
        tuple[Path, tuple[str, ...], tuple[CpSpecItem, ...], str]
    ] = []
    for path in spec_paths:
        try:
            spec_parameters, spec_items = _read_spec(path)
            fingerprint = _spec_fingerprint_sha256(spec_items)
        except CpCsvTripletError as exc:
            if len(spec_paths) > 1:
                raise CpMultiLotSpecBindingRequired(
                    f"{CP_MULTI_LOT_SPEC_BINDING_REQUIRED}: cannot prove a "
                    f"shared normalized Spec because {path.name} is invalid: {exc}"
                ) from exc
            raise
        parsed_specs.append((path, spec_parameters, spec_items, fingerprint))
    fingerprints = {spec[3] for spec in parsed_specs}
    if len(fingerprints) != 1:
        evidence = ", ".join(
            f"{path.name}={fingerprint[:12]}"
            for path, _parameters, _items, fingerprint in parsed_specs
        )
        raise CpMultiLotSpecBindingRequired(
            f"{CP_MULTI_LOT_SPEC_BINDING_REQUIRED}: Cleaner emitted incompatible "
            f"normalized Spec fingerprints; explicit per-Lot binding is required: "
            f"{evidence}"
        )
    spec_path, spec_parameters, declared_spec_items, spec_fingerprint_sha256 = (
        parsed_specs[0]
    )
    normalized_spec_names = _parameter_columns_by_normalized_name(
        spec_parameters, source=spec_path.name
    )
    identity_aliases = {
        "Lot_ID": ("Lot_ID", "LotID"),
        "Wafer_ID": ("Wafer_ID", "WaferID"),
        "Seq": ("Seq",),
        "Bin": ("Bin",),
        "X": ("X",),
        "Y": ("Y",),
    }
    parameters = tuple(item.name for item in declared_spec_items)
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
            measured_by_name = _parameter_columns_by_normalized_name(
                measured_columns, source=path.name
            )
            if set(measured_by_name) != set(normalized_spec_names):
                raise CpCsvTripletError(
                    "CP cleaned parameters do not match the normalized Spec contract "
                    f"after excluding process columns: {path.name}"
                )
            aligned_columns = tuple(
                measured_by_name[
                    _normalized_parameter_name(
                        parameter, field="CP normalized Spec parameter"
                    )
                ]
                for parameter in parameters
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
                        values=tuple(
                            (row[name] or "").strip() for name in aligned_columns
                        ),
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
        lot_keys: dict[str, str] = {}
        for lot_id in lot_ids:
            key = _lot_binding_key(lot_id, field="CP Lot_ID")
            if key in lot_keys:
                raise CpMultiLotSpecBindingRequired(
                    f"{CP_MULTI_LOT_SPEC_BINDING_REQUIRED}: Lot_ID values are "
                    "ambiguous after normalization"
                )
            lot_keys[key] = lot_id
        spec_lot_keys = [_spec_filename_lot_key(path) for path in spec_paths]
        if any(key is None or key not in lot_keys for key in spec_lot_keys):
            raise CpMultiLotSpecBindingRequired(
                f"{CP_MULTI_LOT_SPEC_BINDING_REQUIRED}: cannot prove per-Lot "
                "Spec coverage from Cleaner artifact names"
            )
        covered_lot_keys = {str(key) for key in spec_lot_keys}
        missing_lots = [
            lot_id for key, lot_id in lot_keys.items() if key not in covered_lot_keys
        ]
        if missing_lots:
            raise CpMultiLotSpecBindingRequired(
                f"{CP_MULTI_LOT_SPEC_BINDING_REQUIRED}: normalized Spec evidence "
                f"is missing for Lots: {', '.join(missing_lots)}"
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
        spec_fingerprint_sha256=spec_fingerprint_sha256,
        spec_source_sha256s=tuple(
            hashlib.sha256(path.read_bytes()).hexdigest() for path in spec_paths
        ),
        source_paths=tuple(
            str(path) for path in cleaned_paths + yield_paths + spec_paths
        ),
        lot_ids=tuple(lot_ids),
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
        spec_selection_rule = (
            "SINGLE_LOT_EXPLICIT_SPEC"
            if len(triplet.lot_ids) == 1
            else "MULTI_LOT_SHARED_NORMALIZED_SPEC"
        )
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
                    {
                        "program": program_id,
                        "checksum": triplet.spec_fingerprint_sha256,
                    },
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
                    f"SPEC-{triplet.spec_fingerprint_sha256[:16].upper()}-"
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
                        "sha": triplet.spec_fingerprint_sha256,
                        "metadata": json.dumps(
                            {
                                "spec_binding_contract": "NORMALIZED_SPEC_FINGERPRINT_V1",
                                "binding_scope": "DATASET_VERSION",
                                "spec_fingerprint_sha256": triplet.spec_fingerprint_sha256,
                                "spec_source_sha256s": triplet.spec_source_sha256s,
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
            item_ids = {
                _normalized_parameter_name(
                    row["raw_item_name"], field="stored CP raw item name"
                ): int(row["test_item_id"])
                for row in item_rows
            }

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
                f"spec-fingerprint:{triplet.spec_fingerprint_sha256}:{supplier_code}:"
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
                            "normalized CP Spec"
                        ),
                        "version": version_code,
                        "source": spec_source_ref,
                        "metadata": json.dumps(
                            {
                                "selection_rule": "NORMALIZED_SPEC_FINGERPRINT_V1",
                                "binding_scope": "DATASET_VERSION",
                                "spec_fingerprint_sha256": triplet.spec_fingerprint_sha256,
                                "spec_source_sha256s": triplet.spec_source_sha256s,
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
                            "item": item_ids[
                                _normalized_parameter_name(
                                    item.name, field="CP Spec parameter"
                                )
                            ],
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
                            "spec_selection_rule": spec_selection_rule,
                            "spec_fingerprint_sha256": triplet.spec_fingerprint_sha256,
                            "spec_source_sha256s": triplet.spec_source_sha256s,
                            "lot_ids": triplet.lot_ids,
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
                                "spec_selection_rule": spec_selection_rule,
                                "spec_fingerprint_sha256": triplet.spec_fingerprint_sha256,
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
                            "item": item_ids[
                                _normalized_parameter_name(
                                    name, field="CP measurement parameter"
                                )
                            ],
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
                        "spec_selection_rule": spec_selection_rule,
                        "lot_id": (
                            triplet.lot_ids[0]
                            if len(triplet.lot_ids) == 1
                            else None
                        ),
                        "lot_ids": triplet.lot_ids,
                        "spec_sha256": triplet.spec_sha256,
                        "spec_fingerprint_sha256": triplet.spec_fingerprint_sha256,
                        "spec_source_sha256s": triplet.spec_source_sha256s,
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
