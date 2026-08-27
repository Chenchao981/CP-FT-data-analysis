from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest
from app.infrastructure.existing_cleaner_results import (
    summarize_existing_cleaner_result,
)
from app.infrastructure.existing_cleaner_runner import (
    CleanerArtifact,
    ExistingCleanerRunResult,
)
from app.infrastructure.ft_xlsx_scatter_writer import (
    FtXlsxScatterError,
    FtXlsxScatterWriter,
    parse_ft_xlsx_scatter,
)


def _artifact(role: str, path: Path) -> CleanerArtifact:
    payload = path.read_bytes()
    return CleanerArtifact(
        role, str(path), len(payload), hashlib.sha256(payload).hexdigest()
    )


def _output(tmp_path: Path, *, differing_spec: bool = False) -> tuple[CleanerArtifact, ...]:
    cleaned = tmp_path / "L1_001.xlsx"
    cleaned.write_bytes(b"xlsx-contract-placeholder")
    data = tmp_path / "ft_scatter_data.csv.gz"
    with gzip.open(data, "wt", encoding="utf-8", newline="") as stream:
        stream.write(
            "NUM,lot_ID,Source_ID,P1(V),P2(nA)\n"
            "1,L1,S1,1.5,10\n"
            "2,L1,S1,,11\n"
            "3,L1,S2,1.7,12\n"
        )
    spec = tmp_path / "ft_scatter_spec.csv"
    second_limit = "6" if differing_spec else "5"
    spec.write_text(
        "Source_ID,lot_ID,Parameter,Unit,Low_Limit,High_Limit,Low_Limit_Raw,"
        "High_Limit_Raw,Bias1,Bias2,Test_Condition,Source_File\n"
        "S1,L1,P1(V),V,1,5,>1,<5,(ID)1mA,,ID=1mA,S1_PRODUCT_L1.xlsx\n"
        "S1,L1,P2(nA),nA,,100,,<100,(VDS)40V,,VDS=40V,S1_PRODUCT_L1.xlsx\n"
        f"S2,L1,P1(V),V,1,{second_limit},>1,<5,(ID)1mA,,ID=1mA,S2_PRODUCT_L1.xlsx\n"
        "S2,L1,P2(nA),nA,,100,,<100,(VDS)40V,,VDS=40V,S2_PRODUCT_L1.xlsx\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "ft_scatter_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "data_type": "DC",
                "cleaned_file": cleaned.name,
                "data_file": data.name,
                "spec_file": spec.name,
                "row_count": 3,
                "parameters": ["P1(V)", "P2(nA)"],
                "sources": ["S1", "S2"],
                "lots": ["L1"],
            }
        ),
        encoding="utf-8",
    )
    return (
        _artifact("cleaned", cleaned),
        _artifact("scatter_data", data),
        _artifact("scatter_spec", spec),
        _artifact("scatter_manifest", manifest),
    )


def _approved_output(
    tmp_path: Path,
    *,
    factory_code: str,
    layout: str,
    missing_lot: bool,
    lot_id: str = "FA54-9744",
    product: str = "NCEAP40PT15D(M)-2B00",
) -> tuple[CleanerArtifact, ...]:
    tester = "NCT6528068"
    if layout == "SOURCE_FIRST":
        source_id = (
            f"{tester}_{product}_20250722_070217"
            if missing_lot
            else f"{tester}_{product}_{lot_id}_20250722_070217"
        )
    elif layout == "PRODUCT_FIRST":
        source_id = (
            f"{product}_{tester}_DC_20250722070217"
            if missing_lot
            else f"{product}_{lot_id}_{tester}_DC_20250722070217"
        )
    else:
        raise AssertionError(f"unknown test layout: {layout}")
    cleaned = tmp_path / "cleaned.xlsx"
    cleaned.write_bytes(b"xlsx-contract-placeholder")
    data = tmp_path / "ft_scatter_data.csv.gz"
    with gzip.open(data, "wt", encoding="utf-8", newline="") as stream:
        stream.write(f"NUM,lot_ID,Source_ID,P1(V)\n1,{lot_id},{source_id},1.5\n")
    spec = tmp_path / "ft_scatter_spec.csv"
    spec.write_text(
        "Source_ID,lot_ID,Parameter,Unit,Low_Limit,High_Limit,Low_Limit_Raw,"
        "High_Limit_Raw,Bias1,Bias2,Test_Condition,Source_File\n"
        f"{source_id},{lot_id},P1(V),V,1,5,>1,<5,(ID)1mA,,ID=1mA,{source_id}.xlsx\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "ft_scatter_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "factory_code": factory_code,
                "data_type": "DC",
                "cleaned_file": cleaned.name,
                "data_file": data.name,
                "spec_file": spec.name,
                "row_count": 1,
                "parameters": ["P1(V)"],
                "sources": [source_id],
                "lots": [lot_id],
            }
        ),
        encoding="utf-8",
    )
    return (
        _artifact("cleaned", cleaned),
        _artifact("scatter_data", data),
        _artifact("scatter_spec", spec),
        _artifact("scatter_manifest", manifest),
    )


def test_parse_ft_scatter_reconciles_manifest_specs_and_rows(tmp_path: Path) -> None:
    parsed = parse_ft_xlsx_scatter(_output(tmp_path))

    assert parsed.product_name == "PRODUCT"
    assert parsed.parameters == ("P1(V)", "P2(nA)")
    assert parsed.sources == ("S1", "S2")
    assert parsed.lots == ("L1",)
    assert parsed.rows[0].logical_key == "FT:L1:S1:1"
    assert parsed.rows[1].values == ("", "11")
    assert parsed.spec_items[0].test_condition == "ID=1mA"


def test_parse_ft_scatter_isolates_different_source_specs(tmp_path: Path) -> None:
    parsed = parse_ft_xlsx_scatter(_output(tmp_path, differing_spec=True))

    assert len(parsed.source_specs) == 2
    assert parsed.source_specs[0].sha256 != parsed.source_specs[1].sha256


def test_parse_ft_scatter_rejects_manifest_row_mismatch(tmp_path: Path) -> None:
    artifacts = list(_output(tmp_path))
    manifest = Path(artifacts[-1].path)
    body = json.loads(manifest.read_text(encoding="utf-8"))
    body["row_count"] = 4
    manifest.write_text(json.dumps(body), encoding="utf-8")
    artifacts[-1] = _artifact("scatter_manifest", manifest)

    with pytest.raises(FtXlsxScatterError, match="row_count reconciliation"):
        parse_ft_xlsx_scatter(tuple(artifacts))


def test_parse_ft_scatter_accepts_repeated_identical_source_spec(tmp_path: Path) -> None:
    artifacts = list(_output(tmp_path))
    spec = Path(artifacts[2].path)
    lines = spec.read_text(encoding="utf-8").splitlines()
    spec.write_text("\n".join([*lines, lines[1], ""]), encoding="utf-8")
    artifacts[2] = _artifact("scatter_spec", spec)

    parsed = parse_ft_xlsx_scatter(tuple(artifacts))

    assert parsed.parameters == ("P1(V)", "P2(nA)")


def test_parse_ft_scatter_validates_explicit_riyueguang_identity(
    tmp_path: Path,
) -> None:
    artifacts = list(_output(tmp_path))
    first_source = "NCT6528068_PRODUCT_FA54-9744_20250722_070217"
    second_source = "NCT6528069_PRODUCT_FA54-9744_20250722_113523"
    data = Path(artifacts[1].path)
    with gzip.open(data, "rt", encoding="utf-8") as stream:
        body = stream.read()
    body = body.replace("S1", first_source).replace("S2", second_source)
    body = body.replace("L1", "FA54-9744")
    with gzip.open(data, "wt", encoding="utf-8", newline="") as stream:
        stream.write(body)

    spec = Path(artifacts[2].path)
    body = spec.read_text(encoding="utf-8")
    body = body.replace(
        "S1_PRODUCT_L1.xlsx",
        "NCT6528068_PRODUCT_FA54-9744_20250722_070217.xlsx",
    ).replace(
        "S2_PRODUCT_L1.xlsx",
        "NCT6528069_PRODUCT_FA54-9744_20250722_113523.xlsx",
    )
    body = body.replace("S1", first_source).replace("S2", second_source)
    body = body.replace("L1", "FA54-9744")
    spec.write_text(body, encoding="utf-8")

    manifest = Path(artifacts[3].path)
    manifest_body = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_body.update(
        {
            "factory_code": "RIYUEGUANG",
            "sources": [first_source, second_source],
            "lots": ["FA54-9744"],
        }
    )
    manifest.write_text(json.dumps(manifest_body), encoding="utf-8")
    artifacts[1] = _artifact("scatter_data", data)
    artifacts[2] = _artifact("scatter_spec", spec)
    artifacts[3] = _artifact("scatter_manifest", manifest)

    parsed = parse_ft_xlsx_scatter(tuple(artifacts))

    assert parsed.factory_code == "RIYUEGUANG"
    assert parsed.product_name == "PRODUCT"
    assert [item.tester_id for item in parsed.source_specs] == [
        "NCT6528068",
        "NCT6528069",
    ]


@pytest.mark.parametrize(
    ("factory_code", "layout"),
    [
        ("RIYUEGUANG", "SOURCE_FIRST"),
        ("RIYUEXIN", "PRODUCT_FIRST"),
    ],
)
def test_parse_ft_scatter_accepts_approved_manual_lot_filename_profiles(
    tmp_path: Path, factory_code: str, layout: str
) -> None:
    parsed = parse_ft_xlsx_scatter(
        _approved_output(
            tmp_path,
            factory_code=factory_code,
            layout=layout,
            missing_lot=True,
        )
    )

    assert parsed.factory_code == factory_code
    assert parsed.product_name == "NCEAP40PT15D(M)-2B00"
    assert parsed.lots == ("FA54-9744",)
    assert parsed.source_specs[0].lot_id == "FA54-9744"
    assert parsed.source_specs[0].tester_id == "NCT6528068"


def test_parse_ft_scatter_rejects_unapproved_missing_lot_product(
    tmp_path: Path,
) -> None:
    artifacts = _approved_output(
        tmp_path,
        factory_code="RIYUEXIN",
        layout="PRODUCT_FIRST",
        missing_lot=True,
        product="PRODUCT",
    )

    with pytest.raises(FtXlsxScatterError, match="不符合已批准身份格式"):
        parse_ft_xlsx_scatter(artifacts)


def test_parse_ft_scatter_rejects_invalid_spec_lot_for_missing_lot_profile(
    tmp_path: Path,
) -> None:
    artifacts = _approved_output(
        tmp_path,
        factory_code="RIYUEGUANG",
        layout="SOURCE_FIRST",
        missing_lot=True,
        lot_id="MANUAL-LOT",
    )

    with pytest.raises(FtXlsxScatterError, match="spec row lot_ID"):
        parse_ft_xlsx_scatter(artifacts)


def test_parse_ft_scatter_rejects_source_identity_mismatch(
    tmp_path: Path,
) -> None:
    artifacts = list(
        _approved_output(
            tmp_path,
            factory_code="RIYUEGUANG",
            layout="SOURCE_FIRST",
            missing_lot=True,
        )
    )
    spec = Path(artifacts[2].path)
    body = spec.read_text(encoding="utf-8")
    source_id = json.loads(Path(artifacts[3].path).read_text(encoding="utf-8"))[
        "sources"
    ][0]
    spec.write_text(
        body.replace(f"{source_id},FA54-9744,", "WRONG-SOURCE,FA54-9744,", 1),
        encoding="utf-8",
    )
    artifacts[2] = _artifact("scatter_spec", spec)

    with pytest.raises(FtXlsxScatterError, match="identity differs"):
        parse_ft_xlsx_scatter(tuple(artifacts))


@pytest.mark.parametrize(
    ("layout", "missing_lot"),
    [("PRODUCT_FIRST", False), ("SOURCE_FIRST", True)],
)
def test_ft_summary_uses_controlled_identity_for_product_first_and_manual_lot(
    tmp_path: Path, layout: str, missing_lot: bool
) -> None:
    artifacts = _approved_output(
        tmp_path,
        factory_code="RIYUEXIN",
        layout=layout,
        missing_lot=missing_lot,
    )
    summary = summarize_existing_cleaner_result(
        ExistingCleanerRunResult("FT", "riyuexin", str(tmp_path), artifacts, "ok")
    )

    assert summary["product_name"] == "NCEAP40PT15D(M)-2B00"
    assert summary["lot_id"] == "FA54-9744"
    assert summary["unit_count"] == 1
    assert summary["test_item_count"] == 1


def test_ft_summary_rejects_manifest_factory_different_from_invoked_adapter(
    tmp_path: Path,
) -> None:
    artifacts = _approved_output(
        tmp_path,
        factory_code="RIYUEXIN",
        layout="SOURCE_FIRST",
        missing_lot=True,
    )

    with pytest.raises(RuntimeError, match="manifest factory_code"):
        summarize_existing_cleaner_result(
            ExistingCleanerRunResult(
                "FT",
                "riyueguang",
                str(tmp_path),
                artifacts,
                "ok",
            )
        )


class _CanonicalBoundaryReached(RuntimeError):
    pass


class _CanonicalBoundaryEngine:
    called = False

    def begin(self):
        self.called = True
        raise _CanonicalBoundaryReached


def test_manual_lot_adapter_artifacts_reach_canonical_write_boundary(
    tmp_path: Path,
) -> None:
    artifacts = _approved_output(
        tmp_path,
        factory_code="RIYUEXIN",
        layout="PRODUCT_FIRST",
        missing_lot=True,
    )
    engine = _CanonicalBoundaryEngine()

    with pytest.raises(_CanonicalBoundaryReached):
        FtXlsxScatterWriter(engine).write(  # type: ignore[arg-type]
            job_id=41,
            import_batch_id=17,
            artifacts=artifacts,
        )

    assert engine.called is True
