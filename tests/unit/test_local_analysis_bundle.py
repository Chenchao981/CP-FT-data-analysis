import json
import zipfile

import pytest

from scripts.release.build_local_analysis_bundle import build


def test_bundle_is_relocatable_and_contains_no_runtime_state(tmp_path):
    package = tmp_path / "tool.pyz"
    package.write_bytes(b"official-test-package")
    target = tmp_path / "bundle.zip"
    build(package, target, "http://test-pyms.example:5173")
    with zipfile.ZipFile(target) as bundle:
        assert bundle.testzip() is None
        config = json.loads(bundle.read("config.json"))
        assert config["ft_package"] == "ft_data_cleaner.pyz"
        assert config["allowed_origins"] == ["http://test-pyms.example:5173"]
        assert len(config["ft_package_sha256"]) == 64
        assert not any(
            "__pycache__" in name or "tests/" in name or name.endswith(".log")
            for name in bundle.namelist()
        )
    with pytest.raises(FileExistsError):
        build(package, target, "http://test-pyms.example:5173")


def test_bundle_rejects_origin_with_credentials(tmp_path):
    with pytest.raises(ValueError):
        build(
            tmp_path / "tool.pyz",
            tmp_path / "bundle.zip",
            "https://user:secret@example.com",
        )
