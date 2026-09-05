from __future__ import annotations

from dataclasses import dataclass


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
    def die_identity(self) -> tuple[str, str, int, int]:
        """One die in a cleaned snapshot; Seq is traceability, not identity."""
        return (self.lot_id, self.wafer_id, self.x, self.y)

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
