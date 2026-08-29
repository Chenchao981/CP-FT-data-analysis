from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import pytest
from app.infrastructure.cp_csv_triplet_writer import (
    CP_MULTI_LOT_SPEC_BINDING_REQUIRED,
    CpCsvTripletError,
    CpCsvTripletWriter,
    CpMultiLotSpecBindingRequired,
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


def test_parse_cp_triplet_reconciles_single_lot_cleaned_yield_and_spec(
    tmp_path: Path,
) -> None:
    parsed = parse_cp_csv_triplet(_triplet(tmp_path))

    assert parsed.product_name == "P"
    assert parsed.parameters == ("P1", "P2")
    assert parsed.spec_items[0].test_condition == "10V | 1ms"
    assert parsed.rows[0].logical_key == "CP:L1:1:10:20:1"
    assert parsed.rows[1].values[0] == ""
    assert parsed.pass_count == 1


def test_cp_writer_uses_atomic_draft_stage_without_first_batch_or_current_publish() -> None:
    source = inspect.getsource(CpCsvTripletWriter.write)
    module_source = inspect.getsource(
        __import__(CpCsvTripletWriter.__module__, fromlist=["*"])
    )

    assert "prepare_atomic_stage" in source
    assert "insert_draft_dataset_version" in source
    assert "record_atomic_stage" in source
    assert "PUBLISHED" not in source
    assert "SUPERSEDED" not in source
    assert "SINGLE_LOT_EXPLICIT_SPEC" in module_source
    assert "MULTI_LOT_SHARED_NORMALIZED_SPEC" in module_source
    assert "NORMALIZED_SPEC_FINGERPRINT_V1" in module_source
    assert "FIRST_BATCH" not in module_source
    assert "AND ((product_id=:product) OR (product_id IS NULL" in module_source


def test_parse_cp_triplet_rejects_multiple_lots_when_one_spec_cannot_prove_per_lot_coverage(
    tmp_path: Path,
) -> None:
    artifacts = list(_triplet(tmp_path))
    cleaned_path = Path(artifacts[0].path)
    cleaned_path.write_text(
        "Lot_ID,Wafer_ID,Seq,Bin,X,Y,P1,P2\n"
        "L2,1,1,1,10,20,1E-3,2.5\n"
        "L1,1,1,1,10,20,1E-3,2.5\n",
        encoding="utf-8",
    )
    artifacts[0] = _artifact("cleaned", cleaned_path)
    yield_path = Path(artifacts[1].path)
    yield_path.write_text(
        "Product_Name,Lot_ID,Wafer_ID,Yield,Total,Pass,Bin7\n"
        "P,L2,1,100%,1,1,0\n"
        "P,L1,1,100%,1,1,0\n",
        encoding="utf-8",
    )
    artifacts[1] = _artifact("yield", yield_path)

    with pytest.raises(
        CpMultiLotSpecBindingRequired,
        match="normalized Spec evidence is missing for Lots: L2",
    ) as error:
        parse_cp_csv_triplet(tuple(artifacts))

    assert error.value.error_code == CP_MULTI_LOT_SPEC_BINDING_REQUIRED


def test_parse_cp_triplet_accepts_multiple_lots_when_all_spec_artifacts_have_the_same_normalized_contract(
    tmp_path: Path,
) -> None:
    artifacts = list(_triplet(tmp_path))
    cleaned_path = Path(artifacts[0].path)
    cleaned_path.write_text(
        "Lot_ID,Wafer_ID,Seq,Bin,X,Y,P1,P2\n"
        "L1,1,1,1,10,20,1E-3,2.5\n"
        "L2,1,1,1,10,20,1E-3,2.5\n",
        encoding="utf-8",
    )
    artifacts[0] = _artifact("cleaned", cleaned_path)
    yield_path = Path(artifacts[1].path)
    yield_path.write_text(
        "Product_Name,Lot_ID,Wafer_ID,Yield,Total,Pass,Bin7\n"
        "P,L1,1,100%,1,1,0\n"
        "P,L2,1,100%,1,1,0\n",
        encoding="utf-8",
    )
    artifacts[1] = _artifact("yield", yield_path)
    second_spec = tmp_path / "L2_spec_20260824.csv"
    second_spec.write_text(
        "Parameter,Unit,LimitL,LimitU,Test_Condition\n"
        "P2,V,2.00,5.0,1A   |   2ms\n"
        "P1,A,+0.000,1.0000,10V | 1ms\n",
        encoding="utf-8",
    )
    artifacts.append(_artifact("spec", second_spec))

    parsed = parse_cp_csv_triplet(tuple(artifacts))

    assert parsed.lot_ids == ("L1", "L2")
    assert len(parsed.spec_source_sha256s) == 2
    assert tuple(row.lot_id for row in parsed.rows) == ("L1", "L2")


@pytest.mark.parametrize(
    "second_spec_text",
    [
        (
            "Parameter,Unit,LimitL,LimitU,Test_Condition\n"
            "P1,mA,0,1,10V | 1ms\n"
            "P2,V,2,5,1A | 2ms\n"
        ),
        (
            "Parameter,Unit,LimitL,LimitU,Test_Condition\n"
            "P1,A,0,1.1,10V | 1ms\n"
            "P2,V,2,5,1A | 2ms\n"
        ),
        (
            "Parameter,Unit,LimitL,LimitU,Test_Condition\n"
            "P1,A,0,1,11V | 1ms\n"
            "P2,V,2,5,1A | 2ms\n"
        ),
        (
            "Parameter,Unit,LimitL,LimitU,Test_Condition\n"
            "P3,A,0,1,10V | 1ms\n"
            "P2,V,2,5,1A | 2ms\n"
        ),
    ],
    ids=("unit", "limit", "condition", "parameter"),
)
def test_parse_cp_triplet_rejects_multi_lot_incompatible_normalized_specs(
    tmp_path: Path, second_spec_text: str
) -> None:
    artifacts = list(_triplet(tmp_path))
    cleaned_path = Path(artifacts[0].path)
    cleaned_path.write_text(
        "Lot_ID,Wafer_ID,Seq,Bin,X,Y,P1,P2\n"
        "L1,1,1,1,10,20,1E-3,2.5\n"
        "L2,1,1,1,10,20,1E-3,2.5\n",
        encoding="utf-8",
    )
    artifacts[0] = _artifact("cleaned", cleaned_path)
    yield_path = Path(artifacts[1].path)
    yield_path.write_text(
        "Product_Name,Lot_ID,Wafer_ID,Yield,Total,Pass,Bin7\n"
        "P,L1,1,100%,1,1,0\n"
        "P,L2,1,100%,1,1,0\n",
        encoding="utf-8",
    )
    artifacts[1] = _artifact("yield", yield_path)
    second_spec = tmp_path / "L2_spec_20260824.csv"
    second_spec.write_text(second_spec_text, encoding="utf-8")
    artifacts.append(_artifact("spec", second_spec))

    with pytest.raises(
        CpMultiLotSpecBindingRequired,
        match="incompatible normalized Spec fingerprints",
    ) as error:
        parse_cp_csv_triplet(tuple(artifacts))

    assert error.value.error_code == CP_MULTI_LOT_SPEC_BINDING_REQUIRED


def test_parse_cp_triplet_rejects_ambiguous_normalized_spec_mapping(
    tmp_path: Path,
) -> None:
    artifacts = list(_triplet(tmp_path))
    ambiguous_spec = tmp_path / "L2_spec_20260824.csv"
    ambiguous_spec.write_text(
        "Parameter,Unit,LimitL,LimitU,Test_Condition\n"
        "P1,A,0,1,10V | 1ms\n"
        "Ｐ１,A,0,1,10V | 1ms\n"
        "P2,V,2,5,1A | 2ms\n",
        encoding="utf-8",
    )
    artifacts.append(_artifact("spec", ambiguous_spec))

    with pytest.raises(
        CpMultiLotSpecBindingRequired,
        match="cannot prove a shared normalized Spec",
    ) as error:
        parse_cp_csv_triplet(tuple(artifacts))

    assert error.value.error_code == CP_MULTI_LOT_SPEC_BINDING_REQUIRED


def test_parse_cp_triplet_accepts_multiple_files_for_the_same_single_lot(
    tmp_path: Path,
) -> None:
    artifacts = list(_triplet(tmp_path))
    cleaned = tmp_path / "L1_second_cleaned_20260824.csv"
    cleaned.write_text(
        "Lot_ID,Wafer_ID,Seq,Bin,X,Y,P1,P2\n"
        "L1,2,1,1,10,20,1E-3,2.5\n",
        encoding="utf-8",
    )
    yield_file = tmp_path / "L1_second_yield_20260824.csv"
    yield_file.write_text(
        "Product_Name,Lot_ID,Wafer_ID,Yield,Total,Pass,Bin7\n"
        "P,L1,2,100%,1,1,0\n",
        encoding="utf-8",
    )
    second_spec = tmp_path / "L1_second_spec_20260824.csv"
    second_spec.write_bytes(Path(artifacts[2].path).read_bytes())
    artifacts.extend(
        (
            _artifact("cleaned", cleaned),
            _artifact("yield", yield_file),
            _artifact("spec", second_spec),
        )
    )

    parsed = parse_cp_csv_triplet(tuple(artifacts))

    assert {row.lot_id for row in parsed.rows} == {"L1"}
    assert len(parsed.rows) == 3
    assert parsed.pass_count == 2


def test_parse_cp_triplet_excludes_cont_count_symbol(
    tmp_path: Path,
) -> None:
    artifacts = list(_triplet(tmp_path))
    cleaned_path = Path(artifacts[0].path)
    cleaned_path.write_text(
        "Lot_ID,Wafer_ID,Seq,Bin,X,Y,CONT,P1,P2\n"
        "L1,1,1,1,10,20,0.02,1E-3,2.5\n"
        "L1,1,2,7,11,20,0.03,,3.5\n",
        encoding="utf-8",
    )
    artifacts[0] = _artifact("cleaned", cleaned_path)
    spec_path = Path(artifacts[2].path)
    spec_path.write_text(
        "Parameter,CONT,P1,P2\n"
        "Unit,,A,V\n"
        "LimitU,,1,5\n"
        "LimitL,,0,2\n"
        "TestCond:,,10V,1A\n"
        ",,1ms,2ms\n",
        encoding="utf-8",
    )
    artifacts[2] = _artifact("spec", spec_path)

    parsed = parse_cp_csv_triplet(tuple(artifacts))

    assert parsed.parameters == ("P1", "P2")
    assert tuple(item.name for item in parsed.spec_items) == ("P1", "P2")
    assert parsed.rows[0].values == ("1E-3", "2.5")


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


@pytest.mark.parametrize(
    ("cleaned_header", "cleaned_row", "spec_text", "expected_parameters"),
    [
        (
            "LotID,WaferID,Seq,Bin,X,Y,CONT,TEST_NUM,P1",
            "L1,1,1,1,10,20,1,3,2.5",
            "Parameter,TEST_NUM,P1\nUnit,,V\nLimitL,,0\nLimitU,,5\n",
            ("P1",),
        ),
        (
            "Lot_ID,Wafer_ID,X,Y,Seq,Bin,SITE_NUM,CONT,T_TIME,TEST_NUM,P1",
            "L1,1,0.0,0.0,1.0,1.0,1,1,10,3,2.5",
            "Parameter,TEST_NUM,P1\nUNIT,,V\nLIMIT_LOW,,0\nLIMIT_HIGH,,5\n",
            ("P1",),
        ),
        (
            "Lot_ID,Wafer_ID,X,Y,Seq,Bin,P1",
            "SOURCE_GROUP,1,0,0,1.0,1,2.5",
            "Parameter,Unit,LimitL,LimitU,LSL,USL,Target\nP1,V,0,5,0,5,\n",
            ("P1",),
        ),
    ],
)
def test_parse_cp_triplet_accepts_existing_company_standard_csv_variants(
    tmp_path: Path,
    cleaned_header: str,
    cleaned_row: str,
    spec_text: str,
    expected_parameters: tuple[str, ...],
) -> None:
    cleaned = tmp_path / "source_cleaned_1.csv"
    cleaned.write_text(f"{cleaned_header}\n{cleaned_row}\n", encoding="utf-8")
    spec = tmp_path / "source_spec_1.csv"
    spec.write_text(spec_text, encoding="utf-8")
    yield_file = tmp_path / "source_yield_1.csv"
    yield_file.write_text(
        "Lot_ID,Wafer_ID,Gross_die,Good_die,Yield\n"
        f"{cleaned_row.split(',')[0]},{cleaned_row.split(',')[1]},1,1,100%\n",
        encoding="utf-8",
    )

    parsed = parse_cp_csv_triplet(
        (_artifact("cleaned", cleaned), _artifact("yield", yield_file), _artifact("spec", spec))
    )

    assert parsed.product_name is None
    assert parsed.parameters == expected_parameters
    assert parsed.pass_count == 1
