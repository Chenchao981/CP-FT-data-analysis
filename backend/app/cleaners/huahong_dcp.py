from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

BASE_COLUMNS = ("No.U", "X", "Y", "Bin")
PASS_BIN = 1
MAX_FILE_BYTES = 128 * 1024 * 1024

# Approved from the supplied HuaHong evidence set. Parameter order is contractual.
APPROVED_PARAMETER_SCHEMAS: frozenset[tuple[str, ...]] = frozenset(
    {
        (
            "CONT@D", "CONT@G", "CONT@S", "IGES1", "IEGS1", "VTH1", "VTH2",
            "VCESAT1", "VCESAT2", "VCESAT3", "ICES1", "ICES2", "ICES3",
            "BVCES1", "BVCES2", "IGES2", "IEGS2",
        ),
        (
            "CONT", "IGSS0", "IGSS1", "IGSSR1", "VTH", "BVDSS1", "BVDSS2",
            "IDSS1", "IDSS2", "IGSS2", "IGSSR2",
        ),
        (
            "CONT", "IGSS0", "IGSS1", "IGSSR1", "BVDSS1", "BVDSS2",
            "DELTABV", "IDSS1", "VTH", "RDSON1", "VFSDS", "IGSS2", "IGSSR2",
            "IDSS2",
        ),
        (
            "CONT@D", "CONT@G", "CONT@S", "IGES1", "IGESR1", "VTH1", "VTH2",
            "VCESAT1", "VCESAT2", "VCESAT3", "ICES1", "ICES2", "ICES3",
            "BVCES1", "BVCES2", "IGES2", "IGESR2",
        ),
        (
            "CONT", "IGSS0", "IDSS0", "IGSS1", "IGSSR1", "IDSS1", "IDSS10",
            "VTH", "BVDSS1", "BVDSS2", "DELTABV", "RDSON1", "VFSDS", "IGSS2",
            "IGSSR2", "IDSS3", "IDSS30",
        ),
        (
            "CONT", "IGSS0", "IGSS1", "IGSSR1", "VTH", "BVDSS", "RDSON1",
            "RDSON2", "RDSON3", "VFSDS", "IDSS1", "IDSS2", "IGSS2", "IGSSR2",
        ),
        (
            "CONT@D", "CONT@G", "CONT@S", "IGSS1", "IGSSR1", "IDSS1", "VTH",
            "BVDSS1", "BVDSS2", "BV2-BV1", "RDSON1", "VFSDS", "IGSS2",
            "IGSSR2", "IDSS3",
        ),
        (
            "CONT@D", "CONT@G", "CONT@S", "IGSS1", "IGSSR1", "IDSS1", "VTH1",
            "BVDSS1", "BVDSS2", "BV2-BV1", "RDSON1", "VFSDS", "IGSS2",
            "IGSSR2", "IDSS3",
        ),
        (
            "CONT@D", "CONT@G", "CONT@S", "IGSS1", "IGSSR1", "BVDSS1",
            "BVDSS2", "BV2-BV1", "IDSS1", "VTH1", "IDSS2", "RDSON", "VFSDS",
            "IGSS2", "IGSSR2",
        ),
        (
            "CONT@D", "CONT@G", "CONT@S", "IGSS1", "IGSSR1", "BVDSS1",
            "BVDSS2", "DELTABV", "IDSS1", "VTH", "RDSON1", "VFSDS", "IGSS2",
            "IGSSR2",
        ),
    }
)


class HuaHongFormatError(ValueError):
    """Raised when a file is not an approved HuaHong DCP/TXT contract."""


@dataclass(frozen=True, slots=True)
class EngineeringValue:
    raw: str
    value_base: float | None
    unit_base: str | None


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    name: str
    lower: EngineeringValue
    upper: EngineeringValue
    bias_raw: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MeasurementValue:
    raw: str
    value_numeric: float | None
    status: str


@dataclass(frozen=True, slots=True)
class HuaHongUnitRecord:
    sequence: int
    x: int
    y: int
    soft_bin: int
    overall_result: str
    source_row_no: int
    measurements: tuple[MeasurementValue, ...]


@dataclass(frozen=True, slots=True)
class HuaHongDcpFile:
    source_name: str
    source_sha256: str
    program_name: str
    business_lot_id: str
    lot_number: str
    wafer_number: str
    source_date: date
    source_time: time
    schema_id: str
    parameters: tuple[str, ...]
    source_column_indexes: tuple[int, ...]
    specs: tuple[ParameterSpec, ...]
    units: tuple[HuaHongUnitRecord, ...]

    @property
    def row_count(self) -> int:
        return len(self.units)

    @property
    def pass_count(self) -> int:
        return sum(item.soft_bin == PASS_BIN for item in self.units)

    @property
    def yield_rate(self) -> float:
        return self.pass_count / self.row_count if self.row_count else 0.0

    @property
    def bin_counts(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for item in self.units:
            counts[item.soft_bin] = counts.get(item.soft_bin, 0) + 1
        return counts


_ENGINEERING_VALUE = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*"
    r"(?:(-)|([fpnumkMGTµμ]?)(V|A|OHM|Ohm|ohm|Ω|S|s))$"
)
_BUSINESS_LOT_PATTERNS = (
    re.compile(r"^([A-Z0-9]{4}-[A-Z0-9]{4})-[A-Z0-9]+-\d{6}@(?:20[23]|CP)$"),
    re.compile(r"^([A-Z]\d{6}\.\d{2})-(?:CP|EP)TSTE\d+-\d{6}-\d{6}@CP$"),
)
_PREFIX_FACTORS = {
    "": 1.0,
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "μ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
}


def _schema_id(parameters: tuple[str, ...]) -> str:
    digest = hashlib.sha256("\x1f".join(parameters).encode("utf-8")).hexdigest()[:12]
    return f"HH_DCP_{digest.upper()}"


def _split_tab_line(line: str) -> list[str]:
    return [item.strip() for item in line.split("\t")]


def _parse_engineering_value(raw: str) -> EngineeringValue:
    value = raw.strip()
    if not value:
        return EngineeringValue(raw="", value_base=None, unit_base=None)
    match = _ENGINEERING_VALUE.fullmatch(value)
    if match is None:
        raise HuaHongFormatError(f"invalid engineering value: {value!r}")
    number, dimensionless, prefix, unit = match.groups()
    if dimensionless:
        return EngineeringValue(raw=value, value_base=float(number), unit_base="1")
    assert prefix is not None and unit is not None
    base_unit = "ohm" if unit.lower() == "ohm" or unit == "Ω" else unit.lower()
    if base_unit == "v":
        base_unit = "V"
    elif base_unit == "a":
        base_unit = "A"
    elif base_unit == "s":
        base_unit = "s"
    return EngineeringValue(
        raw=value,
        value_base=float(number) * _PREFIX_FACTORS[prefix],
        unit_base=base_unit,
    )


def _parse_source_date(raw: str) -> date:
    parts = raw.split("/")
    if len(parts) != 3:
        raise HuaHongFormatError(f"invalid source date: {raw!r}")
    first, second, third = (int(item) for item in parts)
    return date(first, second, third) if first > 1900 else date(third, first, second)


def _parse_source_time(raw: str) -> time:
    parts = raw.split(":")
    if len(parts) != 3:
        raise HuaHongFormatError(f"invalid source time: {raw!r}")
    return time(*(int(item) for item in parts))


def _business_lot_id(source_lot_number: str) -> str:
    for pattern in _BUSINESS_LOT_PATTERNS:
        if match := pattern.fullmatch(source_lot_number):
            return match.group(1)
    raise HuaHongFormatError(
        "source Lot number does not match an approved HuaHong identity pattern"
    )


def _metadata_value(line: str, key: str) -> str:
    parts = line.split("\t")
    if len(parts) < 2 or parts[0].strip() != key or not parts[1].strip():
        raise HuaHongFormatError(f"missing or invalid metadata field {key!r}")
    return parts[1].strip()


def _normalize_row(parts: list[str], width: int, row_no: int) -> list[str]:
    if len(parts) < len(BASE_COLUMNS):
        raise HuaHongFormatError(f"row {row_no} has fewer than four identity columns")
    if len(parts) > width and any(parts[width:]):
        raise HuaHongFormatError(f"row {row_no} has unexpected extra values")
    return (parts[:width] + [""] * width)[:width]


def _parse_int(raw: str, field: str, row_no: int) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise HuaHongFormatError(f"row {row_no} has invalid {field}: {raw!r}") from exc
    return value


def _parse_measurement(raw: str, row_no: int, parameter: str) -> MeasurementValue:
    value = raw.strip()
    if not value:
        return MeasurementValue(raw="", value_numeric=None, status="NOT_TESTED")
    try:
        numeric = float(value)
    except ValueError as exc:
        raise HuaHongFormatError(
            f"row {row_no} parameter {parameter!r} has invalid numeric value {value!r}"
        ) from exc
    return MeasurementValue(raw=value, value_numeric=numeric, status="MEASURED")


class HuaHongDcpParser:
    profile_code = "HUAHONG_DCP_TXT"
    profile_version = "1.0"
    pass_bin = PASS_BIN

    def detect(self, path: str | Path) -> bool:
        try:
            self.parse_path(path, include_units=False)
        except (OSError, UnicodeError, HuaHongFormatError):
            return False
        return True

    def parse_path(
        self, path: str | Path, *, include_units: bool = True
    ) -> HuaHongDcpFile:
        source = Path(path)
        return self.parse_bytes(
            source.read_bytes(), source_name=source.name, include_units=include_units
        )

    def parse_bytes(
        self,
        content: bytes,
        *,
        source_name: str,
        include_units: bool = True,
    ) -> HuaHongDcpFile:
        if Path(source_name).suffix.lower() != ".txt":
            raise HuaHongFormatError("HuaHong DCP input must use the .TXT extension")
        size = len(content)
        if size <= 0 or size > MAX_FILE_BYTES:
            raise HuaHongFormatError(f"invalid source file size: {size}")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HuaHongFormatError("source must be valid UTF-8 text") from exc
        return self.parse_text(
            text,
            source_name=source_name,
            source_sha256=hashlib.sha256(content).hexdigest(),
            include_units=include_units,
        )

    def parse_text(
        self,
        text: str,
        *,
        source_name: str = "sample.TXT",
        source_sha256: str | None = None,
        include_units: bool = True,
    ) -> HuaHongDcpFile:
        lines = text.splitlines()
        if len(lines) < 16:
            raise HuaHongFormatError("source has fewer than 16 required lines")

        program_name = _metadata_value(lines[0], "Program name")
        lot_number = _metadata_value(lines[1], "Lot number")
        business_lot_id = _business_lot_id(lot_number)
        wafer_number = _metadata_value(lines[2], "Wafer number")
        source_date = _parse_source_date(_metadata_value(lines[3], "Date"))
        source_time = _parse_source_time(_metadata_value(lines[4], "Time"))
        if lines[5].strip():
            raise HuaHongFormatError("expected a blank separator before the header")

        header = tuple(_split_tab_line(lines[6]))
        if header[:4] != BASE_COLUMNS:
            raise HuaHongFormatError("required identity header is not No.U/X/Y/Bin")
        raw_parameters = header[4:]
        if not raw_parameters or any(not item for item in raw_parameters):
            raise HuaHongFormatError("parameter names must be non-empty")
        if len(raw_parameters) != len(set(raw_parameters)):
            raise HuaHongFormatError("duplicate parameter names are not allowed")
        if raw_parameters not in APPROVED_PARAMETER_SCHEMAS:
            raise HuaHongFormatError("parameter schema is not approved for HuaHong DCP")
        parameter_columns = tuple(
            (column, name)
            for column, name in enumerate(raw_parameters, start=4)
            if name != "CONT"
        )
        parameters = tuple(name for _, name in parameter_columns)

        limit_upper = _normalize_row(_split_tab_line(lines[7]), len(header), 8)
        limit_lower = _normalize_row(_split_tab_line(lines[8]), len(header), 9)
        if limit_upper[0] != "LimitU" or limit_lower[0] != "LimitL":
            raise HuaHongFormatError("LimitU and LimitL rows must immediately follow the header")

        bias_rows: list[list[str]] = []
        for offset in range(6):
            row_no = 10 + offset
            row = _normalize_row(_split_tab_line(lines[9 + offset]), len(header), row_no)
            if row[0] != f"Bias {offset + 1}":
                raise HuaHongFormatError(f"expected Bias {offset + 1} on row {row_no}")
            bias_rows.append(row)

        specs: list[ParameterSpec] = []
        for index, parameter in parameter_columns:
            upper = _parse_engineering_value(limit_upper[index])
            lower = _parse_engineering_value(limit_lower[index])
            if upper.unit_base and lower.unit_base and upper.unit_base != lower.unit_base:
                raise HuaHongFormatError(
                    f"parameter {parameter!r} has incompatible limit units"
                )
            specs.append(
                ParameterSpec(
                    name=parameter,
                    lower=lower,
                    upper=upper,
                    bias_raw=tuple(row[index] for row in bias_rows),
                )
            )

        units: list[HuaHongUnitRecord] = []
        sequences: set[int] = set()
        coordinates: set[tuple[int, int]] = set()
        for index, line in enumerate(lines[15:], start=16):
            if not line.strip():
                continue
            row = _normalize_row(_split_tab_line(line), len(header), index)
            sequence = _parse_int(row[0], "No.U", index)
            x = _parse_int(row[1], "X", index)
            y = _parse_int(row[2], "Y", index)
            soft_bin = _parse_int(row[3], "Bin", index)
            if sequence <= 0:
                raise HuaHongFormatError(f"row {index} has non-positive No.U")
            if soft_bin <= 0:
                raise HuaHongFormatError(f"row {index} has non-positive Bin")
            if sequence in sequences:
                raise HuaHongFormatError(f"duplicate No.U {sequence}")
            if (x, y) in coordinates:
                raise HuaHongFormatError(f"duplicate coordinate ({x}, {y})")
            sequences.add(sequence)
            coordinates.add((x, y))
            measurements = (
                tuple(
                    _parse_measurement(row[column], index, parameter)
                    for column, parameter in parameter_columns
                )
                if include_units
                else ()
            )
            units.append(
                HuaHongUnitRecord(
                    sequence=sequence,
                    x=x,
                    y=y,
                    soft_bin=soft_bin,
                    overall_result="PASS" if soft_bin == PASS_BIN else "FAIL",
                    source_row_no=index,
                    measurements=measurements,
                )
            )
        if not units:
            raise HuaHongFormatError("source contains no Die rows")

        stem = Path(source_name).stem
        if "_" in stem:
            file_lot, file_wafer = stem.rsplit("_", 1)
            if file_lot != lot_number:
                raise HuaHongFormatError("file name Lot does not match source metadata")
            if file_wafer.isdigit() and int(file_wafer) != int(wafer_number):
                raise HuaHongFormatError("file name Wafer does not match source metadata")

        digest = source_sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest()
        return HuaHongDcpFile(
            source_name=source_name,
            source_sha256=digest,
            program_name=program_name,
            business_lot_id=business_lot_id,
            lot_number=lot_number,
            wafer_number=wafer_number,
            source_date=source_date,
            source_time=source_time,
            schema_id=_schema_id(parameters),
            parameters=parameters,
            source_column_indexes=tuple(column for column, _ in parameter_columns),
            specs=tuple(specs),
            units=tuple(units),
        )


def summarize_files(files: Iterable[HuaHongDcpFile]) -> dict[str, object]:
    parsed = tuple(files)
    schemas: dict[str, int] = {}
    bins: dict[int, int] = {}
    row_count = 0
    pass_count = 0
    lots: set[str] = set()
    wafers: set[tuple[str, str]] = set()
    for item in parsed:
        schemas[item.schema_id] = schemas.get(item.schema_id, 0) + 1
        lots.add(item.business_lot_id)
        wafers.add((item.business_lot_id, item.wafer_number))
        row_count += item.row_count
        pass_count += item.pass_count
        for bin_code, count in item.bin_counts.items():
            bins[bin_code] = bins.get(bin_code, 0) + count
    return {
        "file_count": len(parsed),
        "lot_count": len(lots),
        "wafer_count": len(wafers),
        "row_count": row_count,
        "pass_count": pass_count,
        "yield_rate": pass_count / row_count if row_count else 0.0,
        "schema_counts": dict(sorted(schemas.items())),
        "bin_counts": dict(sorted(bins.items())),
    }
