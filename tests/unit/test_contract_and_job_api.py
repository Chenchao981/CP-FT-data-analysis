from __future__ import annotations

from app.domain.jobs import CreateJobRequest
from app.main import create_app
from fastapi.testclient import TestClient

from tests.unit.test_governance_contracts import valid_profile_payload


def test_contract_validation_api() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/contracts/format-profiles/validate",
        json=valid_profile_payload(),
    )
    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_validation_error_has_stable_contract_and_request_id() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/contracts/format-profiles/validate",
        json={"test_stage": "CP"},
        headers={"X-Request-ID": "contract-test-1"},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["request_id"] == "contract-test-1"
    assert response.headers["X-Request-ID"] == "contract-test-1"


def test_job_api_state_machine() -> None:
    client = TestClient(create_app())
    created = client.post(
        "/api/v1/jobs",
        json={
            "import_batch_id": 7,
            "cleaner_release_id": 2,
            "job_type": "PARSE",
            "trigger_type": "MANUAL",
            "requested_by": "tester",
        },
    )
    assert created.status_code == 201
    job_id = created.json()["job_id"]
    assert created.json()["status"] == "QUEUED"

    running = client.post(
        f"/api/v1/jobs/{job_id}/transitions",
        json={"target_status": "RUNNING"},
    )
    assert running.status_code == 200
    assert running.json()["status"] == "RUNNING"

    finished = client.post(
        f"/api/v1/jobs/{job_id}/transitions",
        json={"target_status": "SUCCESS"},
    )
    assert finished.status_code == 200
    assert finished.json()["status"] == "SUCCESS"

    rejected = client.post(
        f"/api/v1/jobs/{job_id}/transitions",
        json={"target_status": "RUNNING"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "INVALID_JOB_TRANSITION"


def test_generic_job_api_cannot_bypass_initial_import_workflow() -> None:
    client = TestClient(create_app())
    blocked = client.post(
        "/api/v1/jobs",
        json={
            "import_batch_id": 7,
            "cleaner_release_id": 2,
            "job_type": "INITIAL_IMPORT",
            "trigger_type": "MANUAL",
            "requested_by": "ignored-client-identity",
        },
    )

    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "INITIAL_IMPORT_ENTRYPOINT_REQUIRED"

    ordinary = client.post(
        "/api/v1/jobs",
        json={
            "import_batch_id": 7,
            "cleaner_release_id": 2,
            "job_type": "PARSE",
            "trigger_type": "MANUAL",
            "requested_by": "tester",
        },
    )
    assert ordinary.status_code == 201
    assert ordinary.json()["job_id"] == 1


def test_generic_transition_api_cannot_mutate_initial_import_job() -> None:
    app = create_app()
    internal_job = app.state.job_service.create(
        CreateJobRequest(
            import_batch_id=7,
            cleaner_release_id=2,
            job_type="INITIAL_IMPORT",
            trigger_type="AUTO",
            requested_by="internal-upload",
            idempotency_key="initial-import:7",
        )
    )
    client = TestClient(app)

    blocked = client.post(
        f"/api/v1/jobs/{internal_job.job_id}/transitions",
        json={"target_status": "RUNNING"},
    )

    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "INITIAL_IMPORT_TRANSITION_RESERVED"
    assert app.state.job_service.get(internal_job.job_id).status == "QUEUED"
