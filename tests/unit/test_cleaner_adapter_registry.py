from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.cleaner_adapters import ExistingCleanerAdapterRegistry
from app.main import create_app


def test_registry_keeps_cp_and_ft_existing_cleaners_separate(tmp_path: Path) -> None:
    cp_root = tmp_path / "cp"
    ft_root = tmp_path / "ft"
    (cp_root / "packaging" / "release").mkdir(parents=True)
    (cp_root / "packaging" / "release" / "app.pyz").touch()
    (ft_root / "packaging" / "release").mkdir(parents=True)
    (ft_root / "packaging" / "release" / "ft_data_cleaner.pyz").touch()

    items = ExistingCleanerAdapterRegistry(str(cp_root), str(ft_root)).descriptors()
    assert [item.test_stage for item in items] == ["CP", "FT"]
    assert all(item.available for item in items)
    assert items[0].source_project == "cp_data_ansys"
    assert items[1].source_project == "data_IGBT_multiple"
    assert items[0].release_package.endswith("app.pyz")
    assert items[1].release_package.endswith("ft_data_cleaner.pyz")


def test_adapter_contract_endpoint_reports_existing_projects() -> None:
    response = TestClient(create_app()).get("/api/v1/contracts/cleaner-adapters")
    assert response.status_code == 200
    assert [item["test_stage"] for item in response.json()] == ["CP", "FT"]
