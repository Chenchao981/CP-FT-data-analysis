from __future__ import annotations

from typing import Any

import pytest
from app.core.errors import DomainError
from app.domain.analytics import AnalyticsContextRequest
from app.infrastructure.formal_spec_context_resolver import (
    formal_spec_context_from_rows,
    resolve_formal_spec_context,
)


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "dataset_version_id": 1001,
        "run_id": 101,
        "lot_id": "LOT-A",
        "test_stage": "FT",
        "test_item_id": 301,
        "event_at_utc": "2026-01-01T00:00:00Z",
        "spec_binding_id": 401,
        "scope_priority": 20,
        "spec_set_id": 501,
        "version_code": "V1",
        "spec_item_id": 601,
    }
    row.update(overrides)
    return row


def test_freezes_every_exact_historic_spec_across_lots_and_parameters() -> None:
    result = formal_spec_context_from_rows(
        (
            _row(),
            _row(run_id=102, lot_id="LOT-B", spec_set_id=502, version_code="V2"),
            _row(test_item_id=302, spec_item_id=602),
        )
    )

    assert result.spec_versions == ("SPEC:501:V1", "SPEC:502:V2")
    assert result.resolved_scope_count == 3
    assert result.no_spec_scope_count == 0


def test_same_priority_ft_bindings_fail_closed() -> None:
    with pytest.raises(DomainError) as error:
        formal_spec_context_from_rows(
            (_row(), _row(spec_binding_id=402, spec_set_id=502))
        )

    assert error.value.code == "ANALYSIS_SPEC_CONTEXT_AMBIGUOUS"


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.sql = ""

    def execute(self, statement: Any, _parameters: Any) -> _Rows:
        self.sql = " ".join(str(statement).split())
        return _Rows(self.rows)


def _request() -> AnalyticsContextRequest:
    return AnalyticsContextRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["VTH"],
    )


def test_query_uses_run_event_time_and_ignores_current_active_flag() -> None:
    connection = _Connection([_row()])

    result = resolve_formal_spec_context(
        connection,
        (
            {
                "dataset_version_id": 1001,
                "test_stage": "FT",
                "spec_set_id": None,
            },
        ),
        _request(),
    )

    assert result.spec_versions == ("SPEC:501:V1",)
    assert "COALESCE(tr.started_at_utc,pr.started_at_utc)" in connection.sql
    assert "SYSUTCDATETIME" not in connection.sql
    assert "sb.active=1" not in connection.sql


def test_selected_scope_without_formal_spec_fails_closed() -> None:
    connection = _Connection(
        [
            _row(
                spec_binding_id=None,
                scope_priority=None,
                spec_set_id=None,
                version_code=None,
                spec_item_id=None,
            )
        ]
    )

    with pytest.raises(DomainError) as error:
        resolve_formal_spec_context(
            connection,
            (
                {
                    "dataset_version_id": 1001,
                    "test_stage": "FT",
                    "spec_set_id": None,
                },
            ),
            _request(),
        )

    assert error.value.code == "ANALYSIS_SPEC_CONTEXT_INCOMPLETE"
