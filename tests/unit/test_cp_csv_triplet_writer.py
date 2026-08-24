from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from app.infrastructure.cp_csv_triplet_writer import (
    CpCsvTripletError,
    parse_cp_csv_triplet,
)
from app.infrastructure.existing_cleaner_runner import CleanerArtifact


def _artifact(role: str, path: Path) -> CleanerArtifact:
    payload = path.read_bytes()
    return CleanerArtifact(role, str(path), len(payload), hashlib.sha256(payload).hexdigest())


def _triplet(tmp_path: Path) -> tuple[CleanerArtifact, ...]:
    cleaned = tmp_path / "L1_cleaned_20260824.csv"
    cleaned.write_text(
        "Lot_ID,Wafer_ID,Seq,Bin,X,Y,P1,P2\n"
        "L1,1,1,1,10,20,1E-3,2.5\n"
        "L1,1,2,7,11,20,,3.5\n",
        encoding="utf-8",
    )
    spec = tmp_path / "L1_spec_20260824.csv"
    spec.write_text(
        "Parameter,P1,P2\n"
        "Unit,A,V\n"
        "LimitU,1,5\n"
        "LimitL,0,2\n"
        "TestCond:,10V,1A\n"
        ",1ms,2ms\n",
        encoding="utf-8",
    )
    yield_file = tmp_path / "L1_yield_20260824.csv"
    yield_file.write_text(
        "Product_Name,Lot_ID,Wafer_ID,Yield,Total,Pass,Bin7\n"
        "P,L1,1,50%,2,1,1\n"
        "P,ALL,ALL,50%,2,1,1\n",
        encoding="utf-8",
    )
    return (
        _artifact("cleaned", cleaned),
        _artifact("yield", yield_file),
        _artifact("spec", spec),
    )


def test_parse_cp_triplet_reconciles_cleaned_yield_and_first_spec(tmp_path: Path) -> None:
    parsed = parse_cp_csv_triplet(_triplet(tmp_path))

    assert parsed.product_name == "P"
    assert parsed.parameters == ("P1", "P2")
    assert parsed.spec_items[0].test_condition == "10V | 1ms"
    assert parsed.rows[0].logical_key == "CP:L1:1:10:20"
    assert parsed.rows[1].values[0] == ""
    assert parsed.pass_count == 1


def test_parse_cp_triplet_rejects_yield_mismatch(tmp_path: Path) -> None:
    artifacts = list(_triplet(tmp_path))
    yield_path = Path(artifacts[1].path)
    yield_path.write_text(
        "Product_Name,Lot_ID,Wafer_ID,Yield,Total,Pass,Bin7\n"
        "P,L1,1,100%,3,3,0\n",
        encoding="utf-8",
    )
    artifacts[1] = _artifact("yield", yield_path)

    with pytest.raises(CpCsvTripletError, match="Total reconciliation"):
        parse_cp_csv_triplet(tuple(artifacts))
