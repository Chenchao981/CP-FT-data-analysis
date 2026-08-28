from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import health
from app.main import create_app


def test_live() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        health,
        "check_database",
        lambda: {
            "database": "TMS_G0_DEV",
            "database_version": "12.0.2000.8",
            "schema_revision": "sql2014_0004",
            "database_server": "LOCALHOST\\SQLEXPRESS",
        },
    )
    client = TestClient(create_app())
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json()["schema_revision"] == "sql2014_0004"


def test_ready_returns_503(monkeypatch) -> None:
    def unavailable() -> dict[str, str]:
        raise RuntimeError("not configured")

    monkeypatch.setattr(health, "check_database", unavailable)
    client = TestClient(create_app())
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
