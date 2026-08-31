from __future__ import annotations

from typing import Any

import pytest
from app.infrastructure.sql_spec_evaluation_materializer import (
    SpecEvaluationMaterializationError,
    materialize_processing_run_spec_evaluations,
)


class _CaptureConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: Any, parameters: dict[str, Any]) -> None:
        self.calls.append((str(statement), parameters))


def _normalized(sql: str) -> str:
    return " ".join(sql.split())


def test_generic_resolver_is_released_effective_context_and_priority_strict() -> None:
    connection = _CaptureConnection()

    materialize_processing_run_spec_evaluations(  # type: ignore[arg-type]
        connection,
        processing_run_id=71,
    )

    assert len(connection.calls) == 1
    sql, parameters = connection.calls[0]
    compact = _normalized(sql)
    assert parameters == {
        "processing_run_id": 71,
        "lock_resource": "TMS_SPEC_EVALUATION_PROCESSING_RUN:71",
    }
    assert "spec_set.status='RELEASED'" in compact
    assert "sb.active=1" in compact
    assert "priority.active=1" in compact
    assert "sb.supplier_id IS NULL OR sb.supplier_id=rc.supplier_id" in compact
    assert "sb.product_id IS NULL OR sb.product_id=rc.product_id" in compact
    assert "sb.test_stage IS NULL OR sb.test_stage=rc.test_stage" in compact
    assert (
        "sb.program_version_id IS NULL OR sb.program_version_id=rc.program_version_id"
    ) in compact
    assert "spec_set.effective_from_utc<=rc.event_time_utc" in compact
    assert "spec_set.effective_to_utc>rc.event_time_utc" in compact
    assert "COALESCE(tr.started_at_utc, pr.started_at_utc) AS event_time_utc" in compact
    assert "tr.ended_at_utc" not in compact
    assert "ORDER BY priority.priority DESC" in compact
    assert "WHERE priority_rank=1" in compact
    assert "generic.top_candidate_count>1" in compact


def test_item_match_limits_and_statuses_are_fail_closed_without_program_limits() -> (
    None
):
    connection = _CaptureConnection()

    materialize_processing_run_spec_evaluations(  # type: ignore[arg-type]
        connection,
        processing_run_id=72,
    )

    compact = _normalized(connection.calls[0][0])
    assert "spec_item.test_item_id=test_item.test_item_id" in compact
    assert "spec_item.unit_code COLLATE Latin1_General_100_BIN2" in compact
    assert "spec_item.condition_json COLLATE Latin1_General_100_BIN2" in compact
    assert "program_lsl" not in compact
    assert "program_usl" not in compact
    for result in (
        "PASS",
        "FAIL",
        "NO_MATCH",
        "CONFIG_AMBIGUOUS",
        "NOT_EVALUATED",
        "INVALID_VALUE",
    ):
        assert f"'{result}'" in compact
    assert "item.lower_operator NOT IN (N'>=', N'>')" in compact
    assert "item.upper_operator NOT IN (N'<=', N'<')" in compact
    assert "item.lsl>item.usl" in compact
    assert "item.lsl IS NULL AND item.usl IS NULL" in compact


def test_explicit_map_is_complete_frozen_and_parameterized() -> None:
    connection = _CaptureConnection()

    materialize_processing_run_spec_evaluations(  # type: ignore[arg-type]
        connection,
        processing_run_id=73,
        explicit_run_spec_set_ids={902: 302, 901: 301},
    )

    sql, parameters = connection.calls[0]
    compact = _normalized(sql)
    assert "INSERT #tms_explicit_run_spec(run_id,spec_set_id) VALUES" in compact
    assert parameters == {
        "processing_run_id": 73,
        "lock_resource": "TMS_SPEC_EVALUATION_PROCESSING_RUN:73",
        "explicit_run_id_0": 901,
        "explicit_spec_set_id_0": 301,
        "explicit_run_id_1": 902,
        "explicit_spec_set_id_1": 302,
    }
    assert "SPEC_EVALUATION_EXPLICIT_RUN_MISMATCH" in compact
    assert "SPEC_EVALUATION_EXPLICIT_MAP_INCOMPLETE" in compact
    assert "spec_set.spec_set_id=rc.explicit_spec_set_id" in compact
    assert "rc.explicit_spec_set_id IS NOT NULL THEN 'RESOLVED'" in compact


def test_materialization_is_transaction_locked_idempotent_and_exactly_current() -> None:
    connection = _CaptureConnection()

    materialize_processing_run_spec_evaluations(  # type: ignore[arg-type]
        connection,
        processing_run_id=74,
    )

    compact = _normalized(connection.calls[0][0])
    assert "sys.sp_getapplock" in compact
    assert "@LockOwner='Transaction'" in compact
    assert "evaluation.evaluation_type='SPEC'" in compact
    assert "evaluation.evaluation_scope_key=N'FORMAL_SPEC'" in compact
    assert "WHERE NOT EXISTS" in compact
    assert "existing.evaluation_type='SPEC'" in compact
    assert "existing.evaluation_scope_key=N'FORMAL_SPEC'" in compact
    assert "SPEC_EVALUATION_CURRENT_INVARIANT_FAILED" in compact
    assert "evaluation_run_id" not in compact
    assert "INSERT mdm.spec_set" not in compact
    assert "INSERT mdm.spec_item" not in compact
    assert "INSERT mdm.spec_binding" not in compact


@pytest.mark.parametrize("processing_run_id", [0, -1, True, 1.5])
def test_rejects_invalid_processing_run_id(processing_run_id: Any) -> None:
    connection = _CaptureConnection()

    with pytest.raises(
        SpecEvaluationMaterializationError,
        match="processing_run_id must be a positive integer",
    ):
        materialize_processing_run_spec_evaluations(  # type: ignore[arg-type]
            connection,
            processing_run_id=processing_run_id,
        )

    assert connection.calls == []


@pytest.mark.parametrize(
    "explicit_map",
    [
        {0: 1},
        {True: 1},
        {1.5: 1},
        {1: 0},
        {1: True},
        {1: 2.5},
    ],
)
def test_rejects_invalid_explicit_map(explicit_map: dict[Any, Any]) -> None:
    connection = _CaptureConnection()

    with pytest.raises(SpecEvaluationMaterializationError, match="positive integers"):
        materialize_processing_run_spec_evaluations(  # type: ignore[arg-type]
            connection,
            processing_run_id=75,
            explicit_run_spec_set_ids=explicit_map,
        )

    assert connection.calls == []
