from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import current_principal
from app.domain.auth import DEVELOPMENT_PRINCIPAL
from app.main import create_app


class SourceService:
    def __init__(self): self.calls = []
    def list(self, principal): return []
    def create(self, principal, config):
        self.calls.append(config)
        return dict(source_definition_id=1, active=False)
    def control(self, principal, source_id, **options):
        self.calls.append((source_id, options))
        return dict(accepted=True)
    def packages(self, principal, source_id, **options): return dict(total=0, items=[])


def client_for(principal=DEVELOPMENT_PRINCIPAL):
    application = create_app()
    service = SourceService()
    application.state.ftp_source_service = service
    application.dependency_overrides[current_principal] = lambda: principal
    return TestClient(application), service


def payload():
    return dict(source_code="FTP_TEST", source_name="测试采集", protocol="FTP", host="127.0.0.1", remote_root="/",
        credential_ref="FTP_TEST", test_stage="FT", factory_code="RIYUEXIN", data_domain_id=1, cleaner_release_id=1,
        package_mode="SINGLE_FILE", allowed_suffixes=[".xlsx"])


def test_create_source_is_paused_and_accepts_only_a_credential_reference():
    client, service = client_for()
    result = client.post("/api/v1/ftp-sources", json=payload())
    assert result.status_code == 201 and not result.json()["active"]
    assert service.calls[0].credential_ref == "FTP_TEST"
    secret = "synthetic-never-persisted"
    rejected = client.post("/api/v1/ftp-sources", json=payload() | dict(password=secret))
    assert rejected.status_code == 422 and secret not in rejected.text
    assert len(service.calls) == 1


@pytest.mark.parametrize("path,method,body", [
    ("", "POST", payload()), ("/1/state", "PATCH", {"active": True}),
    ("/1/scan", "POST", None), ("/1/connection-check", "POST", None),
    ("/1/packages/1/retry", "POST", None), ("/options", "GET", None),
])
def test_non_admin_cannot_configure_or_run_collection(path, method, body):
    ordinary = replace(DEVELOPMENT_PRINCIPAL, roles=(), permissions=frozenset({"DATASET_READ"}))
    client, service = client_for(ordinary)
    result = client.request(method, "/api/v1/ftp-sources" + path, json=body)
    assert result.status_code == 403 and not service.calls


def test_scan_request_is_queued_and_pagination_is_bounded():
    client, service = client_for()
    assert client.post("/api/v1/ftp-sources/9/scan").status_code == 202
    assert service.calls == [(9, dict(scan=True))]
    assert client.get("/api/v1/ftp-sources/9/packages?page=0").status_code == 422
    assert client.get("/api/v1/ftp-sources/9/packages?page_size=10000").status_code == 422
