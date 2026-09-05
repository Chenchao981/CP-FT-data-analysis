from types import SimpleNamespace

from app.core.errors import DomainError
from app.main import create_app
from fastapi.testclient import TestClient


def test_status_uses_exact_formal_contract_and_does_not_expose_paths():
    class Registry:
        def latest_released_for_contract(self, **contract):
            assert contract["cleaner_code"] != "DIANJI_FT_QUICK_PAT_EXISTING"
            assert contract["format_code"]
            if contract["factory_code"] == "JETECH":
                raise DomainError("CLEANER_RELEASE_NOT_AVAILABLE", "private path", 409)
            return SimpleNamespace(
                cleaner_version="v1",
                code_checksum="a" * 64,
                cleaner_release_id=1,
                artifact_uri="private path",
            )

    app = create_app()
    app.state.cleaner_registry = Registry()
    response = TestClient(app).get("/api/v1/contracts/cleaner-capability-status")
    assert response.status_code == 200
    assert "private path" not in response.text
    rows = {row["capability_code"]: row for row in response.json()}
    assert rows["HUAHONG_CP_STANDARD_CLEAN"]["release"]["sha256"] == "a" * 64
    assert rows["JETECH_CP_STANDARD_CLEAN"]["release"] is None
    assert rows["DIANJI_FT_PERSONAL_PAT"]["release_status"] == "PERSONAL_CONTRACT"


def test_status_without_database_does_not_claim_registered_packages():
    app = create_app()
    app.state.cleaner_registry = None
    rows = TestClient(app).get("/api/v1/contracts/cleaner-capability-status").json()
    assert all(row["release"] is None for row in rows)
