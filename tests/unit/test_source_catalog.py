from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.core.errors import DomainError
from app.infrastructure.source_catalog import SourceCatalog, SourceRoot


def _catalog(root: Path) -> SourceCatalog:
    return SourceCatalog(
        (
            SourceRoot(
                "JIEQUN_TEST",
                "杰群测试数据",
                root.resolve(),
                "FT",
                "JIEQUN",
                (".csv",),
            ),
        )
    )


def test_catalog_browses_relative_directories_without_exposing_root(tmp_path: Path) -> None:
    product = tmp_path / "product-a"
    product.mkdir()
    (product / "one.CSV").write_text("value\n1\n", encoding="utf-8")
    catalog = _catalog(tmp_path)

    public = catalog.list_roots()[0]
    assert public["code"] == "JIEQUN_TEST"
    assert "path" not in public
    current, parent, directories = catalog.browse("jiequn_test", ".")
    assert current == "."
    assert parent is None
    assert directories[0].relative_path == "product-a"
    assert directories[0].direct_file_count == 1


def test_manifest_is_stable_and_detects_source_changes(tmp_path: Path) -> None:
    product = tmp_path / "product-a"
    product.mkdir()
    first = product / "one.csv"
    first.write_text("value\n1\n", encoding="utf-8")
    catalog = _catalog(tmp_path)

    before = catalog.build_manifest("JIEQUN_TEST", "product-a")
    again = catalog.build_manifest("JIEQUN_TEST", "product-a")
    assert before.sha256 == again.sha256
    assert before.file_count == 1
    assert json.loads(before.as_json())["files"][0]["relative_path"] == "one.csv"

    (product / "two.csv").write_text("value\n2\n", encoding="utf-8")
    after = catalog.build_manifest("JIEQUN_TEST", "product-a")
    assert after.sha256 != before.sha256
    assert after.file_count == 2


@pytest.mark.parametrize("relative", ["..", "../outside", r"C:\Windows", r"\\server\share"])
def test_catalog_rejects_paths_outside_the_configured_root(
    tmp_path: Path, relative: str
) -> None:
    catalog = _catalog(tmp_path)
    with pytest.raises(DomainError) as captured:
        catalog.resolve_directory("JIEQUN_TEST", relative)
    assert captured.value.code in {"SOURCE_PATH_INVALID", "SOURCE_PATH_ESCAPE"}


def test_environment_contract_accepts_only_p0_jiequn_csv(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "TMS_SOURCE_ROOTS_JSON",
        json.dumps(
            [
                {
                    "code": "JIEQUN_SHARED",
                    "name": "杰群共享目录",
                    "path": str(tmp_path),
                    "test_stage": "FT",
                    "factory_code": "JIEQUN",
                    "allowed_suffixes": ["csv"],
                }
            ],
            ensure_ascii=False,
        ),
    )
    root = SourceCatalog.from_environment().get_root("JIEQUN_SHARED")
    assert root.allowed_suffixes == (".csv",)
