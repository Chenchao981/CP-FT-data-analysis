from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.api.dependencies import current_principal
from app.api.lifecycle import router
from app.core.errors import DomainError
from app.core.exception_handlers import domain_error_handler
from app.domain.auth import Principal
from app.domain.lifecycle import (
    LifecycleArtifact,
    LifecycleArtifactDownload,
    LifecycleExportStatus,
    LifecycleJobReceipt,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _principal(*, user_id: int = 7, admin: bool = False, export: bool = True):
    permissions = {"TASK_CREATE"}
    if export:
        permissions.add("EXPORT_DATA")
    return Principal(
        user_id=user_id,
        login_name=f"user-{user_id}",
        display_name="Owner",
        roles=("SYSTEM_ADMIN",) if admin else ("DATA_OWNER",),
        permissions=frozenset(permissions),
    )


class StubLifecycle:
    def __init__(self, download_path: Path | None = None) -> None:
        self.download_path = download_path
        self.receipts: dict[tuple[str, int, str], LifecycleJobReceipt] = {}

    def _create(self, action, dataset_id, key, principal, cleaner=9):
        if dataset_id == 99 and "SYSTEM_ADMIN" not in principal.roles:
            raise DomainError("DATASET_SCOPE_DENIED", "scope denied", 403)
        scope = (action, dataset_id, key)
        existing = self.receipts.get(scope)
        if existing is not None:
            return replace(existing, created=False)
        receipt = LifecycleJobReceipt(
            job_id=80 + len(self.receipts),
            job_type=("INITIAL_IMPORT" if action == "REPROCESS_UPDATE" else action),
            dataset_id=dataset_id,
            dataset_version_id=6,
            action_type=action,
            status="QUEUED",
            import_batch_id=17,
            cleaner_release_id=cleaner,
            parent_job_id=41,
            idempotency_key=f"internal-{key}",
            created=True,
        )
        self.receipts[scope] = receipt
        return receipt

    def create_export(self, dataset_id, idempotency_key, principal):
        return self._create("EXPORT_LATEST", dataset_id, idempotency_key, principal)

    def create_archive(self, dataset_id, reason, idempotency_key, principal):
        assert len(reason) >= 8
        return self._create(
            "DELETE_TASK", dataset_id, idempotency_key, principal, cleaner=None
        )

    def create_reprocess(self, dataset_id, reason, idempotency_key, principal):
        return self._create(
            "REPROCESS_UPDATE", dataset_id, idempotency_key, principal
        )

    def artifact_download(self, job_id, artifact_id, principal):
        assert principal.can("EXPORT_DATA")
        if self.download_path is None:
            raise DomainError("EXPORT_ARTIFACT_NOT_FOUND", "missing", 404)
        return LifecycleArtifactDownload(
            self.download_path, self.download_path.name, "application/octet-stream"
        )

    def export_status(self, job_id, principal):
        if job_id == 99 and "SYSTEM_ADMIN" not in principal.roles:
            raise DomainError("DATASET_SCOPE_DENIED", "scope denied", 403)
        expires = datetime.now(UTC) + timedelta(hours=1)
        return LifecycleExportStatus(
            job_id=job_id,
            dataset_id=5,
            dataset_version_id=6,
            cleaner_release_id=9,
            status="SUCCESS",
            error_code=None,
            availability="READY",
            expires_at_utc=expires,
            artifacts=(
                LifecycleArtifact(
                    processing_artifact_id=3,
                    job_id=job_id,
                    artifact_role="EXPORT",
                    file_name="latest.xlsx",
                    file_size=8,
                    sha256="a" * 64,
                    expires_at_utc=expires,
                    physical_status="PRESENT",
                ),
            ),
        )


def _client(service, principal: Principal) -> TestClient:
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)
    app.state.lifecycle_service = service
    app.dependency_overrides[current_principal] = lambda: principal
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def test_export_requires_permission_and_replays_same_job_idempotently() -> None:
    service = StubLifecycle()
    denied = _client(service, _principal(export=False)).post(
        "/api/v1/lifecycle/exports",
        json={"dataset_id": 5, "idempotency_key": "request-0001"},
    )
    assert denied.status_code == 403

    client = _client(service, _principal())
    first = client.post(
        "/api/v1/lifecycle/exports",
        json={"dataset_id": 5, "idempotency_key": "request-0001"},
    )
    second = client.post(
        "/api/v1/lifecycle/exports",
        json={"dataset_id": 5, "idempotency_key": "request-0001"},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert first.json()["idempotent_replay"] is False
    assert second.json()["idempotent_replay"] is True
    assert first.json()["idempotency_key"] == "request-0001"


def test_archive_requires_typed_confirmation_reason_and_owner_scope() -> None:
    client = _client(StubLifecycle(), _principal())
    missing_confirmation = client.post(
        "/api/v1/lifecycle/datasets/5/archive",
        json={"reason": "duplicate dataset", "idempotency_key": "archive-0001"},
    )
    short_reason = client.post(
        "/api/v1/lifecycle/datasets/5/archive",
        json={
            "confirmation": "ARCHIVE",
            "reason": "short",
            "idempotency_key": "archive-0001",
        },
    )
    overreach = client.post(
        "/api/v1/lifecycle/datasets/99/archive",
        json={
            "confirmation": "ARCHIVE",
            "reason": "approved duplicate dataset",
            "idempotency_key": "archive-0002",
        },
    )

    assert missing_confirmation.status_code == 422
    assert short_reason.status_code == 422
    assert overreach.status_code == 403
    accepted = client.post(
        "/api/v1/lifecycle/datasets/5/archive",
        json={
            "confirmation": "ARCHIVE",
            "reason": "approved duplicate dataset",
            "idempotency_key": "archive-0003",
        },
    )
    assert accepted.status_code == 202
    assert accepted.json()["action_type"] == "DELETE_TASK"


def test_download_uses_internal_file_response_without_returning_storage_path(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "latest.xlsx"
    artifact.write_bytes(b"download")

    response = _client(StubLifecycle(artifact), _principal()).get(
        "/api/v1/lifecycle/exports/81/artifacts/3/download"
    )

    assert response.status_code == 200
    assert response.content == b"download"
    assert "latest.xlsx" in response.headers["content-disposition"]


def test_export_status_discovers_artifacts_without_exposing_paths_and_checks_owner() -> None:
    client = _client(StubLifecycle(), _principal())

    response = client.get("/api/v1/lifecycle/exports/81")

    assert response.status_code == 200
    body = response.json()
    assert body["availability"] == "READY"
    assert body["artifacts"][0]["artifact_id"] == 3
    assert body["artifacts"][0]["download_url"].endswith("/3/download")
    assert "storage_uri" not in str(body)
    assert "lease_token" not in body
    assert "lease_owner" not in body
    assert "lease_expires_at_utc" not in body
    assert "path" not in str(body).lower()
    denied = client.get("/api/v1/lifecycle/exports/99")
    assert denied.status_code == 403


def test_lifecycle_router_fails_closed_until_main_registers_service() -> None:
    response = _client(None, _principal()).post(
        "/api/v1/lifecycle/exports",
        json={"dataset_id": 5, "idempotency_key": "request-0001"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_NOT_CONFIGURED"
