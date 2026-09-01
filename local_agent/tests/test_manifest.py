from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_agent.errors import ManifestError
from local_agent.manifest import build_local_manifest, manifest_json


def test_manifest_is_stable_relative_and_detects_change(tmp_path: Path) -> None:
    source = tmp_path / "private-source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (source / "b.csv").write_text("b", encoding="utf-8")
    (nested / "a.CSV").write_text("a", encoding="utf-8")
    (source / "ignored.txt").write_text("ignore", encoding="utf-8")

    first = build_local_manifest(
        source, allowed_suffixes=(".csv",), max_files=10
    )
    second = build_local_manifest(
        source, allowed_suffixes=(".csv",), max_files=10
    )

    assert first.sha256 == second.sha256
    assert first.file_count == 2
    assert first.total_bytes == 2
    payload = json.loads(manifest_json(first))
    assert [item["relative_path"] for item in payload["files"]] == [
        "b.csv",
        "nested/a.CSV",
    ]
    assert str(source.resolve()) not in manifest_json(first)

    (nested / "a.CSV").write_text("changed", encoding="utf-8")
    changed = build_local_manifest(
        source, allowed_suffixes=(".csv",), max_files=10
    )
    assert changed.sha256 != first.sha256


def test_manifest_rejects_empty_and_file_limit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ManifestError, match="符合当前工具合同") as empty:
        build_local_manifest(source, allowed_suffixes=(".csv",), max_files=1)
    assert empty.value.code == "LOCAL_SOURCE_EMPTY"

    (source / "a.csv").write_text("1", encoding="utf-8")
    (source / "b.csv").write_text("2", encoding="utf-8")
    with pytest.raises(ManifestError) as too_many:
        build_local_manifest(source, allowed_suffixes=(".csv",), max_files=1)
    assert too_many.value.code == "LOCAL_SOURCE_FILE_LIMIT_EXCEEDED"


def test_manifest_rejects_link_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr("local_agent.manifest._is_link_or_junction", lambda _: True)
    with pytest.raises(ManifestError) as rejected:
        build_local_manifest(source, allowed_suffixes=(".csv",), max_files=1)
    assert rejected.value.code == "LOCAL_SOURCE_LINK_UNSUPPORTED"
