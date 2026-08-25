from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest
from app.infrastructure.existing_cleaner_runner import CleanerArtifact
from app.infrastructure.ft_xlsx_scatter_writer import (
    FtXlsxScatterError,
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


def test_parse_ft_scatter_reconciles_manifest_specs_and_rows(tmp_path: Path) -> None:
    parsed = parse_ft_xlsx_scatter(_output(tmp_path))

    assert parsed.product_name == "PRODUCT"
    assert parsed.parameters == ("P1(V)", "P2(nA)")
    assert parsed.sources == ("S1", "S2")
    assert parsed.lots == ("L1",)
    assert parsed.rows[0].logical_key == "FT:L1:S1:1"
    assert parsed.rows[1].values == ("", "11")
    assert parsed.spec_items[0].test_condition == "ID=1mA"


def test_parse_ft_scatter_rejects_different_source_specs(tmp_path: Path) -> None:
    with pytest.raises(FtXlsxScatterError, match="Source specs differ"):
        parse_ft_xlsx_scatter(_output(tmp_path, differing_spec=True))


def test_parse_ft_scatter_rejects_manifest_row_mismatch(tmp_path: Path) -> None:
    artifacts = list(_output(tmp_path))
    manifest = Path(artifacts[-1].path)
    body = json.loads(manifest.read_text(encoding="utf-8"))
    body["row_count"] = 4
    manifest.write_text(json.dumps(body), encoding="utf-8")
    artifacts[-1] = _artifact("scatter_manifest", manifest)

    with pytest.raises(FtXlsxScatterError, match="row_count reconciliation"):
        parse_ft_xlsx_scatter(tuple(artifacts))
