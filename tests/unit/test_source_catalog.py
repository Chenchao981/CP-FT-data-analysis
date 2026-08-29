from __future__ import annotations

import json
import os
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
    assert root.purpose == "QUICK_ANALYSIS"


def test_environment_contract_supports_scoped_formal_import_roots(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "TMS_SOURCE_ROOTS_JSON",
        json.dumps(
            [
                {
                    "code": "RIYUEXIN_PRODUCTION",
                    "name": "日月新量产 FT",
                    "path": str(tmp_path),
                    "purpose": "FORMAL_IMPORT",
                    "business_domains": ["PRODUCTION"],
                    "test_stage": "FT",
                    "factory_code": "RIYUEXIN",
                    "allowed_suffixes": [".xlsx"],
                }
            ],
            ensure_ascii=False,
        ),
    )

    catalog = SourceCatalog.from_environment()
    roots = catalog.list_roots(
        purpose="FORMAL_IMPORT",
        business_domain="PRODUCTION",
        test_stage="FT",
        factory_code="RIYUEXIN",
    )
    assert [item["code"] for item in roots] == ["RIYUEXIN_PRODUCTION"]
    assert catalog.list_roots(purpose="QUICK_ANALYSIS") == ()
    catalog.require_scope(
        "RIYUEXIN_PRODUCTION",
        purpose="FORMAL_IMPORT",
        business_domain="PRODUCTION",
        test_stage="FT",
        factory_code="RIYUEXIN",
    )
    with pytest.raises(DomainError) as captured:
        catalog.require_scope(
            "RIYUEXIN_PRODUCTION",
            purpose="FORMAL_IMPORT",
            business_domain="ENGINEERING",
            test_stage="FT",
            factory_code="RIYUEXIN",
        )
    assert captured.value.code == "SOURCE_ROOT_SCOPE_MISMATCH"


@pytest.mark.parametrize("configured_path", ["", ".", "relative\\source"])
def test_environment_contract_rejects_empty_or_relative_source_roots(
    monkeypatch, configured_path: str
) -> None:
    monkeypatch.setenv(
        "TMS_SOURCE_ROOTS_JSON",
        json.dumps(
            [
                {
                    "code": "UNSAFE_ROOT",
                    "name": "不安全目录",
                    "path": configured_path,
                    "test_stage": "FT",
                    "factory_code": "JIEQUN",
                    "allowed_suffixes": [".csv"],
                }
            ],
            ensure_ascii=False,
        ),
    )

    with pytest.raises(RuntimeError, match="path (is required|must be absolute)"):
        SourceCatalog.from_environment()


def test_non_recursive_manifest_ignores_nested_ft_files(tmp_path: Path) -> None:
    (tmp_path / "direct.xlsx").write_bytes(b"direct")
    nested = tmp_path / "DVDS"
    nested.mkdir()
    (nested / "nested.xlsx").write_bytes(b"nested")
    catalog = SourceCatalog(
        (
            SourceRoot(
                "FT_ROOT",
                "FT root",
                tmp_path,
                "FT",
                "RIYUEXIN",
                (".xlsx",),
                "FORMAL_IMPORT",
                ("PRODUCTION",),
            ),
        )
    )

    manifest = catalog.build_manifest("FT_ROOT", ".", recursive=False)
    assert [item.relative_path for item in manifest.files] == ["direct.xlsx"]


def test_catalog_rejects_linked_descendants(tmp_path: Path) -> None:
    outside = tmp_path / "real"
    outside.mkdir()
    linked = tmp_path / "linked"
    try:
        os.symlink(outside, linked, target_is_directory=True)
    except OSError:
        pytest.skip("current Windows account cannot create directory symbolic links")

    with pytest.raises(DomainError) as captured:
        _catalog(tmp_path).resolve_directory("JIEQUN_TEST", "linked")
    assert captured.value.code in {
        "SOURCE_PATH_ESCAPE",
        "SOURCE_PATH_LINK_UNSUPPORTED",
    }


@pytest.mark.parametrize("storage_relative", [".", "managed", "managed/nested"])
def test_catalog_rejects_managed_storage_that_overlaps_a_source_root(
    tmp_path: Path, storage_relative: str
) -> None:
    storage = tmp_path / storage_relative
    with pytest.raises(RuntimeError, match="must not overlap TMS_UPLOAD_ROOT"):
        _catalog(tmp_path).assert_storage_separate(storage)


def test_catalog_accepts_managed_storage_outside_source_roots(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    managed = tmp_path / "managed"

    _catalog(source).assert_storage_separate(managed)
