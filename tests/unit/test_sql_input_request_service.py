from __future__ import annotations

import inspect
from contextlib import contextmanager

import pytest
from app.core.errors import DomainError
from app.domain.auth import ALL_PERMISSIONS, Principal
from app.domain.input_requests import ResolveLotInputRequests
from app.infrastructure.sql_input_request_service import (
    SqlProcessingInputRequestService,
)


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def mappings(self):
        return self

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _AccessConnection:
    def __init__(self, *, owner_user_id: int = 7) -> None:
        self.owner_user_id = owner_user_id
        self.statements: list[str] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        parameters = parameters or {}
        self.statements.append(sql)
        if "SELECT b.import_batch_id,b.status" in sql:
            if "OUTER APPLY" in sql:
                assert "b.access_scope='DOMAIN'" in sql
                assert "iam.data_domain_grant" in sql
            else:
                assert "b.access_scope='PERSONAL'" in sql
                assert "iam.data_domain_grant" not in sql
            if (
                not parameters["is_admin"]
                and parameters["user_id"] != self.owner_user_id
            ):
                return _Result()
            return _Result(
                [{"import_batch_id": 41, "status": "NEEDS_INPUT", "latest_job_id": 73}]
            )
        if "FROM ingestion.processing_input_request pir" in sql:
            return _Result(
                [
                    {
                        "input_request_id": 81,
                        "job_id": 73,
                        "source_file_id": 7001,
                        "original_file_name": "private-file.xlsx",
                        "field_code": "LOT_ID",
                        "prompt": "private prompt",
                    }
                ]
            )
        raise AssertionError(sql)


class _AccessEngine:
    def __init__(self, connection: _AccessConnection) -> None:
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection

    @contextmanager
    def begin(self):
        yield self.connection


def _principal(user_id: int, *, admin: bool = False) -> Principal:
    return Principal(
        user_id,
        f"user-{user_id}",
        f"User {user_id}",
        ("SYSTEM_ADMIN",) if admin else ("ENGINEER",),
        ALL_PERMISSIONS if admin else frozenset({"DATASET_READ", "TASK_CREATE"}),
    )


def test_lot_input_resume_creates_atomic_initial_import_job() -> None:
    source = inspect.getsource(SqlProcessingInputRequestService.resolve)

    assert "max_attempts,finalize_protocol" in source
    assert "'ATOMIC_V1'" in source


@pytest.mark.parametrize("principal", (_principal(7), _principal(1, admin=True)))
def test_owner_and_admin_can_read_private_input_request_details(principal) -> None:
    service = SqlProcessingInputRequestService(  # type: ignore[arg-type]
        _AccessEngine(_AccessConnection())
    )

    result = service.list_open(principal, "PRODUCTION", "FT", 41)

    assert result.prompt == "private prompt"
    assert result.requests[0].source_file_id == 7001
    assert result.requests[0].original_file_name == "private-file.xlsx"


@pytest.mark.parametrize("business_domain", ("ENGINEERING", "PRODUCTION"))
def test_non_owner_cannot_read_input_request_details(business_domain: str) -> None:
    service = SqlProcessingInputRequestService(  # type: ignore[arg-type]
        _AccessEngine(_AccessConnection())
    )

    with pytest.raises(DomainError) as error:
        service.list_open(_principal(8), business_domain, "FT", 41)

    assert error.value.code == "IMPORT_BATCH_NOT_FOUND"
    assert error.value.status_code == 404


@pytest.mark.parametrize("business_domain", ("ENGINEERING", "PRODUCTION"))
def test_non_owner_cannot_resolve_input_requests(business_domain: str) -> None:
    connection = _AccessConnection()
    service = SqlProcessingInputRequestService(  # type: ignore[arg-type]
        _AccessEngine(connection)
    )

    with pytest.raises(DomainError) as error:
        service.resolve(
            _principal(8),
            business_domain,
            "FT",
            41,
            ResolveLotInputRequests(
                resolutions=[{"input_request_id": 81, "lot_id": "LOT-001"}],
                reason="test owner boundary",
            ),
        )

    assert error.value.code == "IMPORT_BATCH_NOT_FOUND"
    assert error.value.status_code == 404
    assert len(connection.statements) == 1
