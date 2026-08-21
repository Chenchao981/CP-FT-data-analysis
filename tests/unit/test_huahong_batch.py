from __future__ import annotations

from pathlib import Path
import zipfile

from app.cleaners.huahong_batch import HuaHongBatchInspector
from tests.unit.test_huahong_dcp import source_text


def write_sample(path: Path, lot: str, wafer: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = source_text().replace("FA00-0001-000A-260820@203", lot).replace(
        "Wafer number\t1", f"Wafer number\t{wafer}"
    )
    path.write_text(content, encoding="utf-8")


def test_batch_resolves_product_candidate_from_contract_directory(tmp_path: Path) -> None:
    raw_lot = "FA00-0001-000A-260820@203"
    source = tmp_path / "NCEPRODUCT_FA00-0001@203" / f"{raw_lot}_001.TXT"
    write_sample(source, raw_lot, 1)
    inspected = HuaHongBatchInspector().inspect_directory(tmp_path)
    assert inspected.status == "PASS"
    assert inspected.product_candidates == {"FA00-0001": "NCEPRODUCT"}


def test_batch_accepts_cp_lot_without_optional_product(tmp_path: Path) -> None:
    raw_lot = "FA00-0001-000A-260820@203"
    source = tmp_path / f"{raw_lot}_001" / f"{raw_lot}_001.TXT"
    write_sample(source, raw_lot, 1)
    inspected = HuaHongBatchInspector().inspect_directory(tmp_path)
    assert inspected.status == "PASS"
    assert inspected.product_candidates == {}
    assert inspected.issues == ()


def test_batch_inspects_zip_and_uses_container_product_candidate(tmp_path: Path) -> None:
    raw_lot = "FA00-0001-000A-260820@203"
    archive = tmp_path / "NCEPRODUCT_FA00-0001@203.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr(f"{raw_lot}_001.TXT", source_text())

    inspected = HuaHongBatchInspector().inspect_input(archive)

    assert inspected.status == "PASS"
    assert inspected.product_candidates == {"FA00-0001": "NCEPRODUCT"}
