from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
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


class FtXlsxScatterError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FtSpecItem:
    name: str
    unit: str | None
    lsl: float | None
    usl: float | None
    raw_lsl: str | None
    raw_usl: str | None
    bias1: str | None
    bias2: str | None
    test_condition: str | None


@dataclass(frozen=True, slots=True)
class FtCleanedRow:
    lot_id: str
    source_id: str
    tester_id: str
    source_file: str
    seq_no: int
    values: tuple[str, ...]
    source_row_no: int

    @property
    def logical_key(self) -> str:
        return f"FT:{self.lot_id}:{self.source_id}:{self.seq_no}"


@dataclass(frozen=True, slots=True)
class FtXlsxScatter:
    factory_code: str
    product_name: str
    parameters: tuple[str, ...]
    spec_items: tuple[FtSpecItem, ...]
    source_specs: tuple[FtSourceSpec, ...]
    rows: tuple[FtCleanedRow, ...]
    sources: tuple[str, ...]
    lots: tuple[str, ...]
    spec_sha256: str
    source_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FtSourceSpec:
    source_id: str
    lot_id: str
    tester_id: str
    source_file: str
    items: tuple[FtSpecItem, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class FtCanonicalImportResult:
    processing_run_id: int
    dataset_id: int
    dataset_version_id: int
    dataset_version_no: int
    spec_set_id: int | None
    unit_count: int
    measurement_count: int


@dataclass(frozen=True, slots=True)
class FtArtifactIdentitySummary:
    factory_code: str
    product_name: str
    parameters: tuple[str, ...]
    sources: tuple[str, ...]
    lots: tuple[str, ...]
    row_count: int
    cleaned_file: str


def _number(raw: str, *, field: str, allow_blank: bool = False) -> float | None:
    value = raw.strip()
    if not value and allow_blank:
        return None
    try:
        return float(Decimal(value))
    except (InvalidOperation, ValueError) as exc:
        raise FtXlsxScatterError(f"{field} is not numeric: {raw!r}") from exc


def _artifact_path(
    artifacts: tuple[CleanerArtifact, ...], role: str
) -> Path:
    paths = [Path(item.path) for item in artifacts if item.role == role]
    if len(paths) != 1:
        raise FtXlsxScatterError(f"FT output requires exactly one {role} artifact")
    if not paths[0].is_file():
        raise FtXlsxScatterError(f"FT output file does not exist: {paths[0]}")
    return paths[0]


def _optional(raw: str | None) -> str | None:
    value = (raw or "").strip()
    return value or None


@dataclass(frozen=True, slots=True)
class FtFactoryConfig:
    code: str
    name: str
    program_name: str
    parser_format: str


FT_FACTORY_CONFIGS = {
    "RIYUEXIN": FtFactoryConfig(
        "RIYUEXIN",
        "日月新",
        "日月新 FT DC Cleaner 标准输出",
        "RIYUEXIN_FT_XLSX_SCATTER",
    ),
    "RIYUEGUANG": FtFactoryConfig(
        "RIYUEGUANG",
        "日月光",
        "日月光 FT DC Cleaner 标准输出",
        "RIYUEGUANG_FT_XLSX_SCATTER",
    ),
}
_LOT_PATTERN = r"[A-Z0-9]{4}-\d{4}"
_SOURCE_PATTERN = r"NCT\d+"
_SOURCE_FIRST_PATTERN = re.compile(
    rf"^(?P<source>{_SOURCE_PATTERN})_(?P<product>.+)_(?P<lot>{_LOT_PATTERN})_"
    r"\d{8}_\d{6}$",
    re.IGNORECASE,
)
_RIYUEXIN_PRODUCT_FIRST_PATTERN = re.compile(
    rf"^(?P<product>.+)_(?P<lot>{_LOT_PATTERN})_(?P<source>{_SOURCE_PATTERN})_DC_"
    r"\d{12,14}$",
    re.IGNORECASE,
)
_APPROVED_PRODUCT_PATTERN = r"NCE[A-Z0-9()\-]+"
_SOURCE_FIRST_MISSING_LOT_PATTERN = re.compile(
    rf"^(?P<source>{_SOURCE_PATTERN})_(?P<product>{_APPROVED_PRODUCT_PATTERN})_"
    r"\d{8}_\d{6}$",
    re.IGNORECASE,
)
_RIYUEXIN_PRODUCT_FIRST_MISSING_LOT_PATTERN = re.compile(
    rf"^(?P<product>{_APPROVED_PRODUCT_PATTERN})_(?P<source>{_SOURCE_PATTERN})_DC_"
    r"\d{12,14}$",
    re.IGNORECASE,
)
_LOT_FULL_PATTERN = re.compile(rf"^{_LOT_PATTERN}$", re.IGNORECASE)


def _legacy_product_from_source_file(source_file: str) -> str:
    parts = Path(source_file).stem.split("_")
    if len(parts) < 3 or not parts[1].strip():
        raise FtXlsxScatterError(
            f"FT Source_File cannot provide Product identity: {source_file}"
        )
    return parts[1].strip()


def _factory_identity_from_source_file(
    source_file: str,
    factory_code: str,
    *,
    spec_lot_id: str | None = None,
) -> tuple[str, str, str]:
    stem = Path(source_file).stem
    match = _SOURCE_FIRST_PATTERN.fullmatch(stem)
    if match is None and factory_code == "RIYUEXIN":
        match = _RIYUEXIN_PRODUCT_FIRST_PATTERN.fullmatch(stem)
    if match is not None:
        file_lot = match.group("lot").upper()
        if spec_lot_id is not None and spec_lot_id.strip().upper() != file_lot:
            raise FtXlsxScatterError(
                f"{factory_code} FT Source_File Lot 与 spec row lot_ID 不一致: {source_file}"
            )
        return (
            match.group("source").upper(),
            match.group("product").strip(),
            file_lot,
        )

    missing_lot_match = _SOURCE_FIRST_MISSING_LOT_PATTERN.fullmatch(stem)
    if missing_lot_match is None and factory_code == "RIYUEXIN":
        missing_lot_match = _RIYUEXIN_PRODUCT_FIRST_MISSING_LOT_PATTERN.fullmatch(stem)
    if missing_lot_match is not None:
        manual_lot = (spec_lot_id or "").strip().upper()
        if not _LOT_FULL_PATTERN.fullmatch(manual_lot):
            raise FtXlsxScatterError(
                f"{factory_code} FT 缺 Lot Source_File 必须由 spec row lot_ID 提供已批准批次号: {source_file}"
            )
        return (
            missing_lot_match.group("source").upper(),
            missing_lot_match.group("product").strip(),
            manual_lot,
        )

    raise FtXlsxScatterError(
        f"{factory_code} FT Source_File 不符合已批准身份格式: {source_file}"
    )


def summarize_ft_xlsx_scatter_identity(
    artifacts: tuple[CleanerArtifact, ...],
) -> FtArtifactIdentitySummary:
    """Read summary identity only from the approved manifest/spec contract."""
    cleaned_path = _artifact_path(artifacts, "cleaned")
    data_path = _artifact_path(artifacts, "scatter_data")
    spec_path = _artifact_path(artifacts, "scatter_spec")
    manifest_path = _artifact_path(artifacts, "scatter_manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FtXlsxScatterError("FT scatter manifest is invalid") from exc
    if manifest.get("schema_version") != 1 or manifest.get("data_type") != "DC":
        raise FtXlsxScatterError("FT scatter manifest contract is unsupported")
    factory_code = str(manifest.get("factory_code") or "").strip().upper()
    if factory_code not in FT_FACTORY_CONFIGS:
        raise FtXlsxScatterError(
            "FT scatter summary requires an approved manifest factory_code"
        )
    if manifest.get("cleaned_file") != cleaned_path.name:
        raise FtXlsxScatterError("FT manifest cleaned_file does not match artifact")
    if manifest.get("data_file") != data_path.name:
        raise FtXlsxScatterError("FT manifest data_file does not match artifact")
    if manifest.get("spec_file") != spec_path.name:
        raise FtXlsxScatterError("FT manifest spec_file does not match artifact")

    parameters = tuple(str(value).strip() for value in manifest.get("parameters") or ())
    sources = tuple(str(value).strip() for value in manifest.get("sources") or ())
    lots = tuple(str(value).strip() for value in manifest.get("lots") or ())
    if (
        not parameters
        or any(not value for value in parameters)
        or len(set(parameters)) != len(parameters)
    ):
        raise FtXlsxScatterError("FT manifest parameters are empty or duplicated")
    if not sources or any(not value for value in sources) or len(set(sources)) != len(sources):
        raise FtXlsxScatterError("FT manifest sources are empty or duplicated")
    if not lots or any(not value for value in lots) or len(set(lots)) != len(lots):
        raise FtXlsxScatterError("FT manifest lots are empty or duplicated")
    try:
        row_count = int(manifest.get("row_count"))
    except (TypeError, ValueError) as exc:
        raise FtXlsxScatterError("FT manifest row_count is invalid") from exc
    if row_count < 0:
        raise FtXlsxScatterError("FT manifest row_count is invalid")

    required_spec = {"Source_ID", "lot_ID", "Parameter", "Source_File"}
    spec_keys: set[tuple[str, str, str]] = set()
    identity_by_group: dict[tuple[str, str], tuple[str, str, str]] = {}
    products: set[str] = set()
    with spec_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not required_spec.issubset(set(reader.fieldnames or ())):
            raise FtXlsxScatterError("FT scatter spec identity columns are incomplete")
        for source_row_no, row in enumerate(reader, start=2):
            source_id = (row.get("Source_ID") or "").strip()
            lot_id = (row.get("lot_ID") or "").strip()
            parameter = (row.get("Parameter") or "").strip()
            source_file = Path(row.get("Source_File") or "").name
            if (
                not source_id
                or not lot_id
                or parameter not in parameters
                or not source_file
            ):
                raise FtXlsxScatterError(
                    f"FT scatter spec row {source_row_no} has invalid summary identity"
                )
            file_source, product, file_lot = _factory_identity_from_source_file(
                source_file, factory_code, spec_lot_id=lot_id
            )
            if Path(source_file).stem != source_id or file_lot != lot_id.upper():
                raise FtXlsxScatterError(
                    f"FT Source_File identity differs from spec row {source_row_no}"
                )
            group = (source_id, lot_id)
            identity = (file_source, product, source_file)
            previous = identity_by_group.get(group)
            if previous is not None and previous != identity:
                raise FtXlsxScatterError(
                    f"FT Source/Lot maps to multiple source identities: {group}"
                )
            identity_by_group[group] = identity
            products.add(product)
            spec_keys.add((source_id, lot_id, parameter))

    spec_groups = set(identity_by_group)
    if {source_id for source_id, _ in spec_groups} != set(sources) or {
        lot_id for _, lot_id in spec_groups
    } != set(lots):
        raise FtXlsxScatterError("FT scatter spec Source/Lot differs from manifest")
    expected_spec_keys = {
        (source_id, lot_id, parameter)
        for source_id, lot_id in spec_groups
        for parameter in parameters
    }
    if spec_keys != expected_spec_keys:
        raise FtXlsxScatterError("FT scatter spec does not cover Source/Lot/Parameter")
    if len(products) != 1:
        raise FtXlsxScatterError("FT upload must resolve exactly one Product")
    return FtArtifactIdentitySummary(
        factory_code=factory_code,
        product_name=next(iter(products)),
        parameters=parameters,
        sources=sources,
        lots=lots,
        row_count=row_count,
        cleaned_file=cleaned_path.name,
    )


def parse_ft_xlsx_scatter(
    artifacts: tuple[CleanerArtifact, ...],
) -> FtXlsxScatter:
    cleaned_path = _artifact_path(artifacts, "cleaned")
    data_path = _artifact_path(artifacts, "scatter_data")
    spec_path = _artifact_path(artifacts, "scatter_spec")
    manifest_path = _artifact_path(artifacts, "scatter_manifest")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FtXlsxScatterError("FT scatter manifest is invalid") from exc
    if manifest.get("schema_version") != 1 or manifest.get("data_type") != "DC":
        raise FtXlsxScatterError("FT scatter manifest contract is unsupported")
    explicit_factory_code = str(manifest.get("factory_code") or "").strip().upper()
    if explicit_factory_code and explicit_factory_code not in FT_FACTORY_CONFIGS:
        raise FtXlsxScatterError("FT scatter manifest factory_code is unsupported")
    factory_code = explicit_factory_code or "RIYUEXIN"
    if manifest.get("cleaned_file") != cleaned_path.name:
        raise FtXlsxScatterError("FT manifest cleaned_file does not match artifact")
    if manifest.get("data_file") != data_path.name:
        raise FtXlsxScatterError("FT manifest data_file does not match artifact")
    if manifest.get("spec_file") != spec_path.name:
        raise FtXlsxScatterError("FT manifest spec_file does not match artifact")
    parameters = tuple(str(value).strip() for value in manifest.get("parameters") or ())
    sources = tuple(str(value).strip() for value in manifest.get("sources") or ())
    lots = tuple(str(value).strip() for value in manifest.get("lots") or ())
    if (
        not parameters
        or any(not value for value in parameters)
        or len(set(parameters)) != len(parameters)
    ):
        raise FtXlsxScatterError("FT manifest parameters are empty or duplicated")
    if (
        not sources
        or any(not value for value in sources)
        or len(set(sources)) != len(sources)
    ):
        raise FtXlsxScatterError("FT manifest sources are empty or duplicated")
    if not lots or any(not value for value in lots) or len(set(lots)) != len(lots):
        raise FtXlsxScatterError("FT manifest lots are empty or duplicated")

    required_spec = {
        "Source_ID",
        "lot_ID",
        "Parameter",
        "Unit",
        "Low_Limit",
        "High_Limit",
        "Low_Limit_Raw",
        "High_Limit_Raw",
        "Bias1",
        "Bias2",
        "Test_Condition",
        "Source_File",
    }
    spec_by_key: dict[tuple[str, str, str], FtSpecItem] = {}
    identity_by_group: dict[tuple[str, str], tuple[str, str]] = {}
    products: set[str] = set()
    with spec_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not required_spec.issubset(set(reader.fieldnames or ())):
            raise FtXlsxScatterError("FT scatter spec columns are incomplete")
        for source_row_no, row in enumerate(reader, start=2):
            source_id = (row["Source_ID"] or "").strip()
            lot_id = (row["lot_ID"] or "").strip()
            parameter = (row["Parameter"] or "").strip()
            if not source_id or not lot_id or parameter not in parameters:
                raise FtXlsxScatterError(
                    f"FT scatter spec row {source_row_no} has invalid identity"
                )
            key = (source_id, lot_id, parameter)
            raw_lsl = _optional(row.get("Low_Limit_Raw"))
            raw_usl = _optional(row.get("High_Limit_Raw"))
            item = FtSpecItem(
                name=parameter,
                unit=_optional(row.get("Unit")),
                lsl=_number(
                    row.get("Low_Limit") or "",
                    field=f"spec row {source_row_no} LSL",
                    allow_blank=True,
                ),
                usl=_number(
                    row.get("High_Limit") or "",
                    field=f"spec row {source_row_no} USL",
                    allow_blank=True,
                ),
                raw_lsl=raw_lsl,
                raw_usl=raw_usl,
                bias1=_optional(row.get("Bias1")),
                bias2=_optional(row.get("Bias2")),
                test_condition=_optional(row.get("Test_Condition")),
            )
            previous = spec_by_key.get(key)
            if previous is not None and previous != item:
                raise FtXlsxScatterError(
                    f"conflicting duplicate FT scatter spec identity: {key}"
                )
            spec_by_key[key] = item
            source_file = Path(row["Source_File"] or "").name
            if not source_file:
                raise FtXlsxScatterError(
                    f"FT scatter spec row {source_row_no} has no Source_File"
                )
            if explicit_factory_code:
                file_source, product, file_lot = _factory_identity_from_source_file(
                    source_file, factory_code, spec_lot_id=lot_id
                )
                if (
                    Path(source_file).stem != source_id
                    or file_lot != lot_id.upper()
                ):
                    raise FtXlsxScatterError(
                        f"FT Source_File identity differs from spec row {source_row_no}"
                    )
                products.add(product)
                identity = (file_source, source_file)
            else:
                products.add(_legacy_product_from_source_file(source_file))
                identity = (source_id, source_file)
            group = (source_id, lot_id)
            previous_identity = identity_by_group.get(group)
            if previous_identity is not None and previous_identity != identity:
                raise FtXlsxScatterError(
                    f"FT Source/Lot maps to multiple source files: {group}"
                )
            identity_by_group[group] = identity
    spec_groups = {(source_id, lot_id) for source_id, lot_id, _ in spec_by_key}
    if {source_id for source_id, _ in spec_groups} != set(sources) or {
        lot_id for _, lot_id in spec_groups
    } != set(lots):
        raise FtXlsxScatterError("FT scatter spec Source/Lot differs from manifest")
    expected_spec_keys = {
        (source_id, lot_id, parameter)
        for source_id, lot_id in spec_groups
        for parameter in parameters
    }
    if set(spec_by_key) != expected_spec_keys:
        raise FtXlsxScatterError("FT scatter spec does not cover Source/Lot/Parameter")
    if len(products) != 1:
        raise FtXlsxScatterError("FT upload must resolve exactly one Product")

    source_order = {source_id: index for index, source_id in enumerate(sources)}
    source_specs: list[FtSourceSpec] = []
    for source_id, lot_id in sorted(
        spec_groups, key=lambda item: (source_order.get(item[0], len(sources)), item[1])
    ):
        items = tuple(
            spec_by_key[(source_id, lot_id, parameter)] for parameter in parameters
        )
        tester_id, source_file = identity_by_group[(source_id, lot_id)]
        fingerprint_payload = json.dumps(
            [asdict(item) for item in items],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        source_specs.append(
            FtSourceSpec(
                source_id=source_id,
                lot_id=lot_id,
                tester_id=tester_id,
                source_file=source_file,
                items=items,
                sha256=hashlib.sha256(fingerprint_payload).hexdigest(),
            )
        )
    representative_specs = source_specs[0].items

    rows: list[FtCleanedRow] = []
    seen_keys: set[str] = set()
    with gzip.open(data_path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = tuple(reader.fieldnames or ())
        if columns != ("NUM", "lot_ID", "Source_ID", *parameters):
            raise FtXlsxScatterError("FT scatter data columns differ from manifest")
        for source_row_no, row in enumerate(reader, start=2):
            lot_id = (row["lot_ID"] or "").strip()
            source_id = (row["Source_ID"] or "").strip()
            if (source_id, lot_id) not in spec_groups:
                raise FtXlsxScatterError(
                    f"FT scatter data row {source_row_no} has unknown Source/Lot"
                )
            try:
                seq_no = int(Decimal(row["NUM"] or ""))
            except (InvalidOperation, ValueError) as exc:
                raise FtXlsxScatterError(
                    f"FT scatter data row {source_row_no} has invalid NUM"
                ) from exc
            parsed = FtCleanedRow(
                lot_id=lot_id,
                source_id=source_id,
                tester_id=identity_by_group[(source_id, lot_id)][0],
                source_file=identity_by_group[(source_id, lot_id)][1],
                seq_no=seq_no,
                values=tuple((row[name] or "").strip() for name in parameters),
                source_row_no=source_row_no,
            )
            if parsed.logical_key in seen_keys:
                raise FtXlsxScatterError(
                    f"duplicate FT unit identity: {parsed.logical_key}"
                )
            seen_keys.add(parsed.logical_key)
            for name, raw in zip(parameters, parsed.values, strict=True):
                _number(raw, field=f"row {source_row_no} {name}", allow_blank=True)
            rows.append(parsed)
    if len(rows) != int(manifest.get("row_count") or 0):
        raise FtXlsxScatterError("FT scatter row_count reconciliation failed")
    actual_sources = {row.source_id for row in rows}
    actual_lots = {row.lot_id for row in rows}
    if actual_sources != set(sources) or actual_lots != set(lots):
        raise FtXlsxScatterError("FT scatter manifest Source/Lot reconciliation failed")

    return FtXlsxScatter(
        factory_code=factory_code,
        product_name=next(iter(products)),
        parameters=parameters,
        spec_items=tuple(representative_specs),
        source_specs=tuple(source_specs),
        rows=tuple(rows),
        sources=sources,
        lots=lots,
        spec_sha256=hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        source_paths=(
            str(cleaned_path),
            str(data_path),
            str(spec_path),
            str(manifest_path),
        ),
    )


class FtXlsxScatterWriter:
    def __init__(self, engine: Engine, *, batch_size: int = 5000) -> None:
        self._engine = engine
        self._batch_size = max(batch_size, 1)

    @staticmethod
    def _scalar(connection: Connection, sql: str, parameters: dict[str, Any]) -> int:
        return int(connection.execute(text(sql), parameters).scalar_one())

    def _ensure_spec_profile(
        self,
        connection: Connection,
        *,
        program_id: int,
        program_code: str,
        supplier_id: int,
        product_id: int,
        factory_config: FtFactoryConfig,
        product_name: str,
        parameters: tuple[str, ...],
        source_spec: FtSourceSpec,
    ) -> tuple[int, int, dict[str, int]]:
        version_code = f"SPEC-{source_spec.sha256[:16].upper()}"
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
                "OUTPUT INSERTED.program_version_id VALUES(:program,:version,:raw,:sha,:metadata)",
                {
                    "program": program_id,
                    "version": version_code,
                    "raw": program_code,
                    "sha": source_spec.sha256,
                    "metadata": json.dumps(
                        {
                            "source_spec_rule": "SOURCE_RUN_SPEC_FINGERPRINT",
                            "factory_code": factory_config.code,
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
                            {
                                "text": item.test_condition,
                                "bias1": item.bias1,
                                "bias2": item.bias2,
                            },
                            ensure_ascii=False,
                        ),
                        "column_index": index + 3,
                    }
                    for index, item in enumerate(source_spec.items, start=1)
                ],
            )
        item_rows = connection.execute(
            text(
                "SELECT test_item_id,raw_item_name FROM mdm.test_item_definition "
                "WHERE program_version_id=:version ORDER BY sequence_no"
            ),
            {"version": program_version_id},
        ).mappings().all()
        item_ids = {
            str(row["raw_item_name"]): int(row["test_item_id"])
            for row in item_rows
        }
        if tuple(item_ids) != parameters:
            raise FtXlsxScatterError("stored FT test items differ from Cleaner output")

        source_ref = f"sha256:{source_spec.sha256}:{factory_config.code}"
        spec_set_id = connection.execute(
            text(
                "SELECT spec_set_id FROM mdm.spec_set WHERE product_id=:product "
                "AND test_stage='FT' AND source_ref=:source"
            ),
            {"product": product_id, "source": source_ref},
        ).scalar_one_or_none()
        if spec_set_id is None:
            spec_set_id = self._scalar(
                connection,
                "INSERT mdm.spec_set(product_id,test_stage,spec_name,version_code,status,source_type,source_ref,metadata_json) "
                "OUTPUT INSERTED.spec_set_id VALUES(:product,'FT',:name,:version,'RELEASED','CLEANER_OUTPUT',:source,:metadata)",
                {
                    "product": product_id,
                    "name": f"{product_name} {factory_config.name} FT Spec",
                    "version": version_code,
                    "source": source_ref,
                    "metadata": json.dumps(
                        {
                            "selection_rule": "SOURCE_RUN_SPEC_FINGERPRINT",
                            "factory_code": factory_config.code,
                        },
                        ensure_ascii=False,
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
                            {
                                "text": item.test_condition,
                                "bias1": item.bias1,
                                "bias2": item.bias2,
                            },
                            ensure_ascii=False,
                        ),
                    }
                    for item in source_spec.items
                ],
            )
        binding_id = connection.execute(
            text(
                "SELECT spec_binding_id FROM mdm.spec_binding "
                "WHERE spec_set_id=:spec AND scope_code='PRODUCT_PROGRAM' "
                "AND supplier_id=:supplier AND product_id=:product "
                "AND test_stage='FT' AND program_version_id=:version AND active=1"
            ),
            {
                "spec": spec_set_id,
                "supplier": supplier_id,
                "product": product_id,
                "version": program_version_id,
            },
        ).scalar_one_or_none()
        if binding_id is None:
            connection.execute(
                text(
                    "INSERT mdm.spec_binding(spec_set_id,scope_code,supplier_id,product_id,test_stage,"
                    "program_version_id,active) VALUES(:spec,'PRODUCT_PROGRAM',:supplier,:product,'FT',:version,1)"
                ),
                {
                    "spec": spec_set_id,
                    "supplier": supplier_id,
                    "product": product_id,
                    "version": program_version_id,
                },
            )
        return int(program_version_id), int(spec_set_id), item_ids

    def write(
        self,
        *,
        job_id: int,
        import_batch_id: int,
        lease_token: str,
        artifacts: tuple[CleanerArtifact, ...],
        finalize_summary: Mapping[str, Any],
    ) -> FtCanonicalImportResult:
        output = parse_ft_xlsx_scatter(artifacts)
        measurement_count = len(output.rows) * len(output.parameters)
        with self._engine.begin() as connection:
            preparation = prepare_atomic_stage(
                connection,
                job_id=job_id,
                import_batch_id=import_batch_id,
                lease_token=lease_token,
                artifacts=artifacts,
            )
            context = preparation.context
            if context["test_stage"] != "FT":
                raise FtXlsxScatterError("FT writer received a non-FT upload task")
            if context["output_contract_version"] != "FT_XLSX_SCATTER_V1":
                raise FtXlsxScatterError("FT Cleaner output contract is not supported")
            if preparation.existing is not None:
                existing = preparation.existing
                return FtCanonicalImportResult(
                    processing_run_id=existing.processing_run_id,
                    dataset_id=existing.dataset_id,
                    dataset_version_id=existing.dataset_version_id,
                    dataset_version_no=existing.dataset_version_no,
                    spec_set_id=existing.spec_set_id,
                    unit_count=existing.unit_count,
                    measurement_count=existing.measurement_count,
                )

            context_factory = str(context["factory_code"]).strip().upper()
            context_factory = {
                "日月新": "RIYUEXIN",
                "ASE": "RIYUEGUANG",
                "日月光": "RIYUEGUANG",
            }.get(context_factory, context_factory)
            factory_config = FT_FACTORY_CONFIGS.get(context_factory)
            if factory_config is None:
                raise FtXlsxScatterError(
                    f"FT factory is not supported by this writer: {context_factory}"
                )
            if output.factory_code != factory_config.code:
                raise FtXlsxScatterError(
                    "FT Cleaner factory identity differs from import batch"
                )
            supplier_code = factory_config.code
            supplier_id = connection.execute(
                text("SELECT supplier_id FROM mdm.supplier WHERE supplier_code=:code"),
                {"code": supplier_code},
            ).scalar_one_or_none()
            if supplier_id is None:
                supplier_id = self._scalar(
                    connection,
                    "INSERT mdm.supplier(supplier_code,supplier_name,supplier_type,active) "
                    "OUTPUT INSERTED.supplier_id VALUES(:code,:name,'OSAT',1)",
                    {"code": supplier_code, "name": factory_config.name},
                )
            product_id = connection.execute(
                text("SELECT product_id FROM mdm.product WHERE product_code=:code"),
                {"code": output.product_name},
            ).scalar_one_or_none()
            if product_id is None:
                product_id = self._scalar(
                    connection,
                    "INSERT mdm.product(product_code,product_name,active) OUTPUT INSERTED.product_id "
                    "VALUES(:code,:name,1)",
                    {"code": output.product_name, "name": output.product_name},
                )
            observe_product_crosswalk(
                connection,
                supplier_id=int(supplier_id),
                product_id=int(product_id),
                test_stage="FT",
                raw_product_code=output.product_name,
            )

            program_code = str(context["output_contract_version"])
            program_id = connection.execute(
                text(
                    "SELECT test_program_id FROM mdm.test_program WHERE supplier_id=:supplier "
                    "AND product_id=:product AND test_stage='FT' AND program_code=:program"
                ),
                {"supplier": supplier_id, "product": product_id, "program": program_code},
            ).scalar_one_or_none()
            if program_id is None:
                program_id = self._scalar(
                    connection,
                    "INSERT mdm.test_program(supplier_id,product_id,test_stage,program_code,program_name,active) "
                    "OUTPUT INSERTED.test_program_id VALUES(:supplier,:product,'FT',:program,:name,1)",
                    {
                        "supplier": supplier_id,
                        "product": product_id,
                        "program": program_code,
                        "name": factory_config.program_name,
                    },
                )
            profile_cache: dict[str, tuple[int, int, dict[str, int]]] = {}
            profiles_by_source: dict[
                tuple[str, str], tuple[int, int, dict[str, int]]
            ] = {}
            for source_spec in output.source_specs:
                profile = profile_cache.get(source_spec.sha256)
                if profile is None:
                    profile = self._ensure_spec_profile(
                        connection,
                        program_id=int(program_id),
                        program_code=program_code,
                        supplier_id=int(supplier_id),
                        product_id=int(product_id),
                        factory_config=factory_config,
                        product_name=output.product_name,
                        parameters=output.parameters,
                        source_spec=source_spec,
                    )
                    profile_cache[source_spec.sha256] = profile
                profiles_by_source[(source_spec.source_id, source_spec.lot_id)] = profile
            spec_set_ids = sorted({profile[1] for profile in profile_cache.values()})
            dataset_spec_set_id = spec_set_ids[0] if len(spec_set_ids) == 1 else None

            parser_format = factory_config.parser_format
            parser_profile_id = connection.execute(
                text(
                    "SELECT parser_profile_id FROM ingestion.parser_profile "
                    "WHERE format_code=:format AND parser_version='1.0'"
                ),
                {"format": parser_format},
            ).scalar_one_or_none()
            if parser_profile_id is None:
                parser_profile_id = self._scalar(
                    connection,
                    "INSERT ingestion.parser_profile(format_code,supplier_id,test_stage,parser_name,parser_version,"
                    "canonical_model_version,detect_rules_json,active,is_default) OUTPUT INSERTED.parser_profile_id "
                    "VALUES(:format,:supplier,'FT',:parser,'1.0','1.0',:rules,1,1)",
                    {
                        "format": parser_format,
                        "supplier": supplier_id,
                        "parser": program_code,
                        "rules": json.dumps(
                            {"identity": ["Source_ID", "lot_ID", "NUM"]}
                        ),
                    },
                )
            processing_run_id = self._scalar(
                connection,
                "INSERT ingestion.processing_run(job_id,source_file_id,parser_profile_id,parser_version,"
                "canonical_model_version,status,is_current,row_count_input,unit_count_output,measurement_count_output,"
                "started_at_utc,finished_at_utc,metadata_json) OUTPUT INSERTED.processing_run_id "
                "VALUES(:job,:source,:parser,'1.0','1.0','READY',0,:units,:units,:measurements,"
                "SYSUTCDATETIME(),SYSUTCDATETIME(),:metadata)",
                {
                    "job": job_id,
                    "source": preparation.source_file_id,
                    "parser": parser_profile_id,
                    "units": len(output.rows),
                    "measurements": measurement_count,
                    "metadata": json.dumps(
                        {
                            "output_contract": program_code,
                            "business_domain": context["business_domain"],
                            "source_paths": output.source_paths,
                            "spec_set_ids": spec_set_ids,
                            "spec_selection_rule": "SOURCE_RUN_SPEC_FINGERPRINT",
                            "pass_fail_available": False,
                            "atomic_finalize_summary": dict(finalize_summary),
                        },
                        ensure_ascii=False,
                    ),
                },
            )
            run_ids: dict[tuple[str, str], int] = {}
            for source_spec in output.source_specs:
                source_key = (source_spec.source_id, source_spec.lot_id)
                program_version_id, spec_set_id, _item_ids = profiles_by_source[
                    source_key
                ]
                run_ids[(source_spec.lot_id, source_spec.source_id)] = self._scalar(
                    connection,
                    "INSERT test.test_run(processing_run_id,supplier_id,product_id,program_version_id,test_stage,"
                    "lot_id,tester_id,run_attempt_no,timezone_resolution,timestamp_source,metadata_json) "
                    "OUTPUT INSERTED.run_id VALUES(:processing,:supplier,:product,:program,'FT',:lot,:source,0,"
                    "'UNKNOWN','UNKNOWN',:metadata)",
                    {
                        "processing": processing_run_id,
                        "supplier": supplier_id,
                        "product": product_id,
                        "program": program_version_id,
                        "lot": source_spec.lot_id,
                        "source": source_spec.tester_id,
                        "metadata": json.dumps(
                            {
                                "spec_set_id": spec_set_id,
                                "source_id": source_spec.source_id,
                                "source_file": source_spec.source_file,
                            },
                            ensure_ascii=False,
                        ),
                    },
                )
            insert_unit = text(
                "INSERT test.unit_result(run_id,logical_unit_key,attempt_no,unit_sequence,vendor_unit_id,"
                "overall_result,source_row_no,metadata_json) VALUES(:run,:key,0,:seq,:vendor,'UNKNOWN',:source_row,:metadata)"
            )
            unit_parameters = [
                {
                    "run": run_ids[(row.lot_id, row.source_id)],
                    "key": row.logical_key,
                    "seq": row.seq_no,
                    "vendor": str(row.seq_no),
                    "source_row": row.source_row_no,
                    "metadata": json.dumps(
                        {
                            "source_id": row.source_id,
                            "source_file": row.source_file,
                            "tester_id": row.tester_id,
                            "lot_id": row.lot_id,
                        },
                        ensure_ascii=False,
                    ),
                }
                for row in output.rows
            ]
            for start in range(0, len(unit_parameters), self._batch_size):
                connection.execute(
                    insert_unit, unit_parameters[start : start + self._batch_size]
                )
            unit_rows = connection.execute(
                text(
                    "SELECT ur.logical_unit_key,ur.unit_id FROM test.unit_result ur "
                    "JOIN test.test_run tr ON tr.run_id=ur.run_id "
                    "WHERE tr.processing_run_id=:processing"
                ),
                {"processing": processing_run_id},
            ).mappings().all()
            unit_ids = {
                str(row["logical_unit_key"]): int(row["unit_id"])
                for row in unit_rows
            }
            if len(unit_ids) != len(output.rows):
                raise FtXlsxScatterError("FT unit identity reconciliation failed")

            insert_measurement = text(
                "INSERT test.measurement(unit_id,test_item_id,value_numeric,value_text,raw_value,measurement_status,"
                "tester_pass_flag,source_column_index) VALUES(:unit,:item,:numeric,NULL,:raw,:status,NULL,:column)"
            )
            measurement_parameters: list[dict[str, Any]] = []
            for row in output.rows:
                item_ids = profiles_by_source[(row.source_id, row.lot_id)][2]
                for index, (name, raw) in enumerate(
                    zip(output.parameters, row.values, strict=True), start=1
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
                            "column": index + 3,
                        }
                    )
                    if len(measurement_parameters) >= self._batch_size:
                        connection.execute(insert_measurement, measurement_parameters)
                        measurement_parameters.clear()
            if measurement_parameters:
                connection.execute(insert_measurement, measurement_parameters)

            dataset_code = f"FT-BATCH-{import_batch_id}"
            dataset_id = connection.execute(
                text("SELECT dataset_id FROM dataset.dataset WHERE dataset_code=:code"),
                {"code": dataset_code},
            ).scalar_one_or_none()
            if dataset_id is None:
                dataset_id = self._scalar(
                    connection,
                    "INSERT dataset.dataset(dataset_code,dataset_name,dataset_type,test_stage,supplier_id,product_id,"
                    "owner_user_id) OUTPUT INSERTED.dataset_id VALUES(:code,:name,'FT_DETAIL','FT',:supplier,:product,:owner)",
                    {
                        "code": dataset_code,
                        "name": f"{context['business_domain']} FT {output.product_name} {'/'.join(output.lots)}",
                        "supplier": supplier_id,
                        "product": product_id,
                        "owner": context["owner_user_id"],
                    },
                )
            version_id, version_no = insert_draft_dataset_version(
                connection,
                dataset_id=int(dataset_id),
                import_batch_id=import_batch_id,
                unit_count=len(output.rows),
                measurement_count=measurement_count,
                spec_set_id=dataset_spec_set_id,
                metadata_json=json.dumps(
                    {
                        "spec_selection_rule": "SOURCE_RUN_SPEC_FINGERPRINT",
                        "spec_set_ids": spec_set_ids,
                        "spec_bindings": [
                            {
                                "lot_id": source_spec.lot_id,
                                "source_id": source_spec.source_id,
                                "source_file": source_spec.source_file,
                                "tester_id": source_spec.tester_id,
                                "program_version_id": profiles_by_source[
                                    (source_spec.source_id, source_spec.lot_id)
                                ][0],
                                "spec_set_id": profiles_by_source[
                                    (source_spec.source_id, source_spec.lot_id)
                                ][1],
                            }
                            for source_spec in output.source_specs
                        ],
                        "pass_fail_available": False,
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
        return FtCanonicalImportResult(
            processing_run_id=processing_run_id,
            dataset_id=int(dataset_id),
            dataset_version_id=version_id,
            dataset_version_no=version_no,
            spec_set_id=(
                int(dataset_spec_set_id)
                if dataset_spec_set_id is not None
                else None
            ),
            unit_count=len(output.rows),
            measurement_count=measurement_count,
        )
