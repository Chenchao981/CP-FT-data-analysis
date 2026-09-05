from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


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
    source_parameter_present: bool = True


@dataclass(frozen=True, slots=True)
class FtCleanedRow:
    lot_id: str
    source_id: str
    tester_id: str | None
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
    tester_id: str | None
    source_file: str
    items: tuple[FtSpecItem, ...]
    sha256: str
    identity_metadata: Mapping[str, Any] | None = None
