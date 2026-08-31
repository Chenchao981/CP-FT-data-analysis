from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event, Lock
from typing import Any

import app.infrastructure.sql_parameter_relationship_service as relationship_module
import pytest
from app.core.errors import DomainError
from app.domain.parameter_relationship import ParameterRelationshipRequest
from app.infrastructure.sql_parameter_relationship_service import (
    SqlParameterRelationshipService,
    _source_identity,
)
from pydantic import ValidationError


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def one(self):
        if len(self._rows) != 1:
            raise AssertionError(f"expected one row, received {len(self._rows)}")
        return self._rows[0]

    def all(self):
        return self._rows


class _Result:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def mappings(self):
        return _Mappings(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class _Engine:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.connect_calls = 0

    @contextmanager
    def connect(self):
        self.connect_calls += 1
        yield self.connection


def _point_row(
    unit_id: int,
    *,
    x_value: float,
    y_value: float | None = None,
    x_status: str = "MEASURED",
    y_status: str = "MEASURED",
    run_id: int = 101,
    started_at_utc: str | None = None,
    source_sequence: int | None = None,
) -> dict[str, Any]:
    row = {
        "run_id": run_id,
        "started_at_utc": started_at_utc,
        "metadata_json": '{"source_id":"SOURCE-A"}',
        "tester_id": "TESTER-A",
        "program_version_id": 201,
        "program_version": "PROGRAM-V1",
        "lot_id": "LOT-A",
        "wafer_id": "W01",
        "unit_id": unit_id,
        "source_sequence": unit_id if source_sequence is None else source_sequence,
        "sequence_no": unit_id if source_sequence is None else source_sequence,
        "x_value": x_value,
        "x_status": x_status,
        "x_lsl": 1.0,
        "x_usl": 9.0,
        "x_lower_operator": ">=",
        "x_upper_operator": "<=",
    }
    if y_value is not None:
        row.update(
            {
                "y_value": y_value,
                "y_status": y_status,
                "y_lsl": 2.0,
                "y_usl": 8.0,
                "y_lower_operator": ">=",
                "y_upper_operator": "<=",
            }
        )
    return row


class _RelationshipConnection:
    def __init__(
        self,
        *,
        scatter_count: int = 3,
        scatter_oos_count: int = 1,
        scatter_oos_rows: list[dict[str, Any]] | None = None,
        scatter_sample_rows: list[dict[str, Any]] | None = None,
        identity_rows: list[dict[str, Any]] | None = None,
        formal_spec_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.scatter_count = scatter_count
        self.scatter_oos_count = scatter_oos_count
        self.scatter_oos_rows = scatter_oos_rows or [
            _point_row(3, x_value=11.0, y_value=6.0, x_status="OVER_RANGE")
        ]
        self.scatter_sample_rows = scatter_sample_rows or [
            _point_row(1, x_value=1.0, y_value=2.0),
            _point_row(2, x_value=2.0, y_value=4.0),
        ]
        self.identity_rows = identity_rows or self._identities()
        self.formal_spec_rows = (
            formal_spec_rows if formal_spec_rows is not None else self._formal_specs()
        )
        self.executed: list[tuple[str, dict[str, object]]] = []

    @staticmethod
    def _identities() -> list[dict[str, Any]]:
        return [
            {
                "run_id": 101,
                "run_program_version_id": 201,
                "test_item_id": 301,
                "program_version_id": 201,
                "step_code": "STEP_X",
                "sequence_no": 1,
                "raw_item_name": "PX",
                "canonical_parameter_code": "PX",
                "unit_code": "V",
                "program_lsl": 100.0,
                "program_usl": 500.0,
                "condition_json": '{"text":"VGS=0V","bias1":"1V"}',
            },
            {
                "run_id": 101,
                "run_program_version_id": 201,
                "test_item_id": 302,
                "program_version_id": 201,
                "step_code": "STEP_Y",
                "sequence_no": 2,
                "raw_item_name": "PY",
                "canonical_parameter_code": "PY",
                "unit_code": "A",
                "program_lsl": 200.0,
                "program_usl": 800.0,
                "condition_json": '{"bias1":"2V","text":"VDS=5V"}',
            },
        ]

    @staticmethod
    def _formal_specs() -> list[dict[str, Any]]:
        return [
            {
                "run_id": 101,
                "run_program_version_id": 201,
                "item_program_version_id": 201,
                "test_item_id": 301,
                "lot_id": "LOT-A",
                "raw_item_name": "PX",
                "spec_set_id": 7,
                "version_code": "FORMAL-V1",
                "spec_item_id": 701,
                "unit_code": "V",
                "lsl": 1.0,
                "usl": 9.0,
                "lower_operator": ">=",
                "upper_operator": "<=",
                "condition_json": '{"text":"VGS=0V","bias1":"1V"}',
            },
            {
                "run_id": 101,
                "run_program_version_id": 201,
                "item_program_version_id": 201,
                "test_item_id": 302,
                "lot_id": "LOT-A",
                "raw_item_name": "PY",
                "spec_set_id": 7,
                "version_code": "FORMAL-V1",
                "spec_item_id": 702,
                "unit_code": "A",
                "lsl": 2.0,
                "usl": 8.0,
                "lower_operator": ">=",
                "upper_operator": "<=",
                "condition_json": '{"bias1":"2V","text":"VDS=5V"}',
            },
        ]

    def execute(self, statement, parameters=None):
        sql = str(statement)
        values = dict(parameters or {})
        self.executed.append((sql, values))
        if "REL_CONTEXT" in sql:
            return _Result(
                rows=[
                    {
                        "dataset_version_id": 10,
                        "dataset_id": int(values["dataset"]),
                        "version_no": int(values["version"]),
                        "status": "PUBLISHED",
                        "is_current": True,
                        "spec_set_id": None,
                        "input_batch_id": 9001,
                        "dataset_name": "FT Dataset",
                        "test_stage": "FT",
                        "supplier_id": 5,
                        "product_id": 6,
                        "product_name": "PRODUCT-A",
                        "spec_version": None,
                    }
                ]
            )
        if "REL_SOURCE" in sql:
            return _Result(
                rows=[
                    {
                        "run_id": 101,
                        "metadata_json": '{"source_id":"SOURCE-A"}',
                        "tester_id": "TESTER-A",
                        "program_version_id": 201,
                        "program_version": "PROGRAM-V1",
                        "lot_id": "LOT-A",
                        "wafer_id": "W01",
                    }
                ]
            )
        if "REL_IDENTITY" in sql:
            assert values["relationship_parameters"] == ("PX", "PY")
            return _Result(rows=self.identity_rows)
        if "REL_FORMAL_SPEC" in sql:
            assert values["selected_test_item_ids"] == (301, 302)
            return _Result(rows=self.formal_spec_rows)
        if "REL_DUPLICATE" in sql:
            assert values["selected_test_item_ids"] == (301, 302)
            return _Result()
        if "REL_COUNTS" in sql:
            assert values["selected_test_item_ids"] == (301, 302)
            return _Result(
                rows=[
                    {
                        "input_units": 3,
                        "included_units": 3,
                        "pass_count": 2,
                        "fail_count": 1,
                        "unknown_count": 0,
                        "abort_count": 0,
                        "selected_measurements": 6,
                        "null_measurements": 0,
                    }
                ]
            )
        if "REL_SCATTER_COMBINED" in sql:
            max_points = int(values["combined_max_points"])
            combined: list[dict[str, Any]] = []
            for rank, row in enumerate(
                self.scatter_oos_rows[: self.scatter_oos_count], start=1
            ):
                combined.append(
                    {
                        **row,
                        "is_out_of_spec": 1,
                        "candidate_count": self.scatter_count,
                        "out_of_spec_count": self.scatter_oos_count,
                        "class_rank": rank,
                    }
                )
            if self.scatter_oos_count <= max_points:
                in_spec_count = self.scatter_count - self.scatter_oos_count
                budget = max_points - self.scatter_oos_count
                stride = max(1, -(-in_spec_count // budget)) if budget else 1
                expected = -(-in_spec_count // stride) if budget else 0
                sample_rows = list(self.scatter_sample_rows[:expected])
                while len(sample_rows) < expected:
                    unit_id = len(sample_rows) + 1
                    sample_rows.append(
                        _point_row(
                            unit_id,
                            x_value=float(unit_id),
                            y_value=float(unit_id) + 1.0,
                        )
                    )
                for rank, row in enumerate(sample_rows, start=1):
                    combined.append(
                        {
                            **row,
                            "is_out_of_spec": 0,
                            "candidate_count": self.scatter_count,
                            "out_of_spec_count": self.scatter_oos_count,
                            "class_rank": rank,
                        }
                    )
            return _Result(rows=combined[: int(values["combined_fetch_limit"])])
        if "REL_SCATTER_COUNT" in sql:
            assert values["x_test_item_ids"] == (301,)
            assert values["y_test_item_ids"] == (302,)
            return _Result(
                rows=[
                    {
                        "candidate_count": self.scatter_count,
                        "out_of_spec_count": self.scatter_oos_count,
                    }
                ]
            )
        if "REL_SCATTER_OOS" in sql:
            return _Result(rows=self.scatter_oos_rows)
        if "REL_SCATTER_SAMPLE" in sql:
            return _Result(
                rows=self.scatter_sample_rows[: int(values["sample_budget"])]
            )
        if "REL_TREND_COUNT" in sql:
            is_x = values["x_test_item_ids"] == (301,)
            return _Result(
                rows=[
                    {
                        "candidate_count": 3,
                        "out_of_spec_count": 1 if is_x else 0,
                    }
                ]
            )
        if "REL_TREND_OOS" in sql:
            if values["x_test_item_ids"] == (301,):
                return _Result(
                    rows=[
                        _point_row(
                            3,
                            x_value=11.0,
                            x_status="OVER_RANGE",
                        )
                    ]
                )
            return _Result()
        if "REL_TREND_SAMPLE" in sql:
            if values["x_test_item_ids"] == (301,):
                rows = [
                    _point_row(1, x_value=1.0),
                    _point_row(2, x_value=2.0),
                ]
            else:
                rows = [
                    _point_row(1, x_value=2.0),
                    _point_row(2, x_value=4.0),
                    _point_row(3, x_value=6.0),
                ]
            return _Result(rows=rows[: int(values["sample_budget"])])
        if "REL_CORRELATION_GROUPS" in sql:
            return _Result(
                rows=[
                    {
                        "run_id": 101,
                        "metadata_json": '{"source_id":"SOURCE-A"}',
                        "tester_id": "TESTER-A",
                        "program_version_id": 201,
                        "program_version": "PROGRAM-V1",
                        "lot_id": "LOT-A",
                        "wafer_id": "W01",
                    }
                ]
            )
        if "REL_CORRELATION" in sql:
            x_ids = values["x_test_item_ids"]
            y_ids = values["y_test_item_ids"]
            if x_ids == y_ids == (301,):
                sums = (6.0, 6.0, 14.0, 14.0, 14.0)
            elif x_ids == y_ids == (302,):
                sums = (12.0, 12.0, 56.0, 56.0, 56.0)
            else:
                sums = (6.0, 12.0, 14.0, 56.0, 28.0)
            return _Result(
                rows=[
                    {
                        "run_id": 101,
                        "metadata_json": '{"source_id":"SOURCE-A"}',
                        "tester_id": "TESTER-A",
                        "program_version_id": 201,
                        "program_version": "PROGRAM-V1",
                        "lot_id": "LOT-A",
                        "wafer_id": "W01",
                        "pair_count": 3,
                        "sum_x": sums[0],
                        "sum_y": sums[1],
                        "sum_x2": sums[2],
                        "sum_y2": sums[3],
                        "sum_xy": sums[4],
                    }
                ]
            )
        raise AssertionError(sql)


class _StageMismatchConnection(_RelationshipConnection):
    def execute(self, statement, parameters=None):
        sql = str(statement)
        values = dict(parameters or {})
        if "REL_CONTEXT" in sql and int(values["dataset"]) == 2:
            self.executed.append((sql, values))
            return _Result(
                rows=[
                    {
                        "dataset_version_id": 20,
                        "dataset_id": 2,
                        "version_no": 1,
                        "status": "PUBLISHED",
                        "is_current": True,
                        "spec_set_id": 90,
                        "input_batch_id": 9002,
                        "dataset_name": "CP Dataset",
                        "test_stage": "CP",
                        "supplier_id": 5,
                        "product_id": 6,
                        "product_name": "PRODUCT-A",
                        "spec_version": "SPEC-V1",
                    }
                ]
            )
        return super().execute(statement, parameters)


class _FormalOperatorMismatchConnection(_RelationshipConnection):
    def execute(self, statement, parameters=None):
        sql = str(statement)
        values = dict(parameters or {})
        if "REL_FORMAL_SPEC" in sql and int(values["dataset"]) == 2:
            self.executed.append((sql, values))
            return _Result(
                rows=[
                    {
                        **row,
                        "upper_operator": "<"
                        if row["raw_item_name"] == "PX"
                        else row["upper_operator"],
                    }
                    for row in self._formal_specs()
                ]
            )
        return super().execute(statement, parameters)


class _FormalIdentityDifferenceConnection(_RelationshipConnection):
    def execute(self, statement, parameters=None):
        sql = str(statement)
        values = dict(parameters or {})
        if "REL_FORMAL_SPEC" in sql and int(values["dataset"]) == 2:
            self.executed.append((sql, values))
            return _Result(
                rows=[
                    {
                        **row,
                        "spec_set_id": 8,
                        "version_code": "FORMAL-V2",
                        "spec_item_id": int(row["spec_item_id"]) + 100,
                    }
                    for row in self._formal_specs()
                ]
            )
        return super().execute(statement, parameters)


class _EightDatasetBatchConnection(_RelationshipConnection):
    def execute(self, statement, parameters=None):
        sql = str(statement)
        values = dict(parameters or {})
        if "REL_MULTI_CONTEXT_IDENTITIES" in sql:
            self.executed.append((sql, values))
            rows: list[dict[str, Any]] = []
            for ordinal in range(1, 9):
                dataset_id = int(values[f"multi_dataset_{ordinal}"])
                for identity in self._identities():
                    rows.append(
                        {
                            "ordinal_no": ordinal,
                            "dataset_version_id": 100 + ordinal,
                            "dataset_id": dataset_id,
                            "version_no": 1,
                            "status": "PUBLISHED",
                            "is_current": True,
                            "spec_set_id": None,
                            "input_batch_id": 9000 + ordinal,
                            "dataset_name": f"FT Dataset {ordinal}",
                            "test_stage": "FT",
                            "supplier_id": 5,
                            "product_id": 6,
                            "product_name": "PRODUCT-A",
                            "spec_version": None,
                            **identity,
                        }
                    )
            return _Result(rows=rows)
        if "REL_MULTI_COUNTS_DUPLICATE" in sql:
            self.executed.append((sql, values))
            return _Result(
                rows=[
                    {
                        "ordinal_no": ordinal,
                        "input_units": 3,
                        "included_units": 3,
                        "pass_count": 2,
                        "fail_count": 1,
                        "unknown_count": 0,
                        "abort_count": 0,
                        "selected_measurements": 6,
                        "null_measurements": 0,
                        "has_duplicate": 0,
                    }
                    for ordinal in range(1, 9)
                ]
            )
        if "REL_SCATTER_MULTI_COUNT" in sql:
            self.executed.append((sql, values))
            return _Result(
                rows=[
                    {
                        "ordinal_no": ordinal,
                        "input_units": 3,
                        "pass_count": 2,
                        "fail_count": 1,
                        "unknown_count": 0,
                        "abort_count": 0,
                        "selected_measurements": 6,
                        "null_measurements": 0,
                        "has_duplicate": 0,
                        "candidate_count": 3,
                        "out_of_spec_count": 0,
                    }
                    for ordinal in range(1, 9)
                ]
            )
        if "REL_SCATTER_MULTI_FETCH" in sql:
            self.executed.append((sql, values))
            return _Result(
                rows=[
                    {
                        **_point_row(
                            ordinal * 10 + rank,
                            x_value=float(rank),
                            y_value=float(rank) + 1.0,
                        ),
                        "dataset_ordinal": ordinal,
                        "is_out_of_spec": 0,
                        "class_rank": rank,
                    }
                    for ordinal in range(1, 9)
                    for rank in range(1, 4)
                ]
            )
        return super().execute(statement, parameters)


class _BlockingEightDatasetBatchConnection(_EightDatasetBatchConnection):
    def __init__(self) -> None:
        super().__init__(scatter_oos_count=0)
        self.preflight_started = Event()
        self.release_preflight = Event()

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "REL_SCATTER_MULTI_COUNT" in sql:
            self.preflight_started.set()
            if not self.release_preflight.wait(timeout=5):
                raise AssertionError("timed out waiting to release multi preflight")
        return super().execute(statement, parameters)


class _BlockingScatterConnection(_RelationshipConnection):
    def __init__(
        self,
        *,
        scatter_count: int = 3,
        expected_combined_starts: int = 1,
        fail_combined: bool = False,
    ) -> None:
        super().__init__(scatter_count=scatter_count)
        self.expected_combined_starts = expected_combined_starts
        self.fail_combined = fail_combined
        self.combined_started = Event()
        self.release_combined = Event()
        self.combined_calls = 0
        self._combined_lock = Lock()

    def execute(self, statement, parameters=None):
        result = super().execute(statement, parameters)
        if "REL_SCATTER_COMBINED" in str(statement):
            with self._combined_lock:
                self.combined_calls += 1
                if self.combined_calls >= self.expected_combined_starts:
                    self.combined_started.set()
            if not self.release_combined.wait(timeout=5):
                raise AssertionError("timed out waiting to release combined scatter")
            if self.fail_combined:
                raise DomainError(
                    "ANALYSIS_TEST_FAILURE",
                    "combined scatter failed",
                    409,
                    details=[{"phase": "scatter"}],
                )
        return result


class _BlockingContextConnection(_RelationshipConnection):
    def __init__(self, *, expected_context_starts: int = 2) -> None:
        super().__init__()
        self.expected_context_starts = expected_context_starts
        self.context_started = Event()
        self.release_context = Event()
        self.context_calls = 0
        self._context_lock = Lock()

    def execute(self, statement, parameters=None):
        result = super().execute(statement, parameters)
        if "REL_CONTEXT" in str(statement):
            with self._context_lock:
                self.context_calls += 1
                if self.context_calls >= self.expected_context_starts:
                    self.context_started.set()
            if not self.release_context.wait(timeout=5):
                raise AssertionError(
                    "timed out waiting to release relationship context"
                )
        return result


class _TrackingRelationshipFlightEvent:
    def __init__(self, wait_started: Event, *, expected_waiters: int = 1) -> None:
        self._event = Event()
        self._wait_started = wait_started
        self._expected_waiters = expected_waiters
        self._waiter_count = 0
        self._waiter_lock = Lock()

    def wait(self, timeout=None):
        with self._waiter_lock:
            self._waiter_count += 1
            if self._waiter_count >= self._expected_waiters:
                self._wait_started.set()
        return self._event.wait(timeout)

    def set(self) -> None:
        self._event.set()


class _OrderedTrendConnection(_RelationshipConnection):
    def execute(self, statement, parameters=None):
        sql = str(statement)
        values = dict(parameters or {})
        if "REL_TREND_OOS" in sql:
            if values["x_test_item_ids"] == (301,):
                return _Result(
                    rows=[
                        _point_row(
                            3,
                            x_value=11.0,
                            x_status="OVER_RANGE",
                            run_id=102,
                            started_at_utc="2026-01-02T00:00:00+00:00",
                            source_sequence=1,
                        )
                    ]
                )
            return _Result()
        if "REL_TREND_SAMPLE" in sql:
            if values["x_test_item_ids"] == (301,):
                rows = [
                    _point_row(
                        2,
                        x_value=2.0,
                        run_id=101,
                        started_at_utc="2026-01-01T00:00:00+00:00",
                        source_sequence=1,
                    ),
                    _point_row(
                        1,
                        x_value=1.0,
                        run_id=101,
                        started_at_utc="2026-01-01T00:00:00+00:00",
                        source_sequence=1,
                    ),
                ]
            else:
                rows = [
                    _point_row(
                        unit_id,
                        x_value=float(unit_id),
                        run_id=101,
                        started_at_utc="2026-01-01T00:00:00+00:00",
                        source_sequence=1,
                    )
                    for unit_id in (3, 2, 1)
                ]
            return _Result(rows=rows[: int(values["sample_budget"])])
        return super().execute(statement, parameters)


class _MissingPairCorrelationConnection(_RelationshipConnection):
    def execute(self, statement, parameters=None):
        sql = str(statement)
        values = dict(parameters or {})
        if (
            "REL_CORRELATION" in sql
            and "REL_CORRELATION_GROUPS" not in sql
            and values.get("x_test_item_ids") == (302,)
            and values.get("y_test_item_ids") == (302,)
        ):
            return _Result()
        return super().execute(statement, parameters)


def _request(**updates: Any) -> ParameterRelationshipRequest:
    payload: dict[str, Any] = {
        "datasets": [{"dataset_id": 1, "version_no": 1}],
        "x_parameter": "PX",
        "y_parameters": ["PY"],
        "analyses": ["SCATTER", "TREND"],
        "group_by": "SOURCE",
        "max_points": 100,
    }
    payload.update(updates)
    return ParameterRelationshipRequest.model_validate(payload)


class _ApprovedCorrelationRuleService:
    def __init__(self, *, minimum_sample_size: int = 2) -> None:
        self.minimum_sample_size = minimum_sample_size
        self.calls: list[dict[str, object]] = []

    def approved_rule_parameters(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {
            "missing_value_policy": "PAIRWISE_EXCLUDE_AND_COUNT",
            "retest_policy": "EACH_ATTEMPT",
            "outlier_policy": "MARK_ONLY",
            "minimum_sample_size": self.minimum_sample_size,
        }


class _DeniedCorrelationRuleService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def approved_rule_parameters(self, **kwargs):
        self.calls.append(dict(kwargs))
        raise DomainError(
            "ANALYSIS_RULE_NOT_APPROVED",
            "correlation rule is not approved for this exact scope",
            409,
        )


def test_relationship_returns_exact_identity_scatter_trend_and_drilldown() -> None:
    connection = _RelationshipConnection()

    result = SqlParameterRelationshipService(
        _Engine(connection)  # type: ignore[arg-type]
    ).relationship(_request())

    assert result.contract_version == "PARAMETER_RELATIONSHIP_V1"
    assert result.dataset_context.test_stage == "FT"
    assert result.counts.input_units == result.counts.included_units == 3
    assert result.filter_summary.parameters == ("PX", "PY")
    assert result.sampling_summary.sampled is False
    assert result.sampling_summary.original_points == 9
    assert result.sampling_summary.returned_points == 9
    assert result.sampling_summary.preserved_out_of_spec_points == 2
    assert len(result.items) == 1
    item = result.items[0]
    assert item.group_key == "SOURCE:SOURCE-A"
    assert {identity.name for identity in item.identities} == {"PX", "PY"}
    assert {
        identity.name: (identity.formal_lsl, identity.formal_usl)
        for identity in item.identities
    } == {
        "PX": (1.0, 9.0),
        "PY": (2.0, 8.0),
    }
    assert result.rule_context.spec_versions == ("SPEC:7:FORMAL-V1",)
    assert len(item.scatter_points) == 3
    assert len(item.trend_points) == 6
    assert any(point.x_out_of_spec for point in item.scatter_points)
    assert all(point.drilldown_key.startswith("UNIT:") for point in item.scatter_points)
    assert all(
        "tid.raw_item_name IN" not in sql
        for sql, _ in connection.executed
        if "REL_SCATTER" in sql or "REL_TREND" in sql
    )
    oos_queries = [
        (sql, parameters)
        for sql, parameters in connection.executed
        if "REL_SCATTER_COUNT" in sql or "REL_TREND_COUNT" in sql
    ]
    assert oos_queries
    assert all(
        "program_lsl" not in sql and "program_usl" not in sql for sql, _ in oos_queries
    )
    assert any(
        parameters.get("x_lsl") == 1.0 and parameters.get("x_usl") == 9.0
        for _, parameters in oos_queries
    )
    formal_sql = next(sql for sql, _ in connection.executed if "REL_FORMAL_SPEC" in sql)
    assert "REL_FORMAL_SPEC_SEEK" in formal_sql
    assert "EXISTS(SELECT 1 FROM test.unit_result spec_ur" in formal_sql
    assert "sp.active=1" in formal_sql


def test_relationship_formal_spec_keeps_unit_scope_for_unit_level_filters() -> None:
    connection = _RelationshipConnection()

    SqlParameterRelationshipService(
        _Engine(connection)  # type: ignore[arg-type]
    ).relationship(
        _request(
            analyses=["SCATTER"],
            filters={"wafer_ids": ["W01"]},
        )
    )

    formal_sql = next(sql for sql, _ in connection.executed if "REL_FORMAL_SPEC" in sql)
    assert "REL_FORMAL_SPEC_SEEK" not in formal_sql
    assert "JOIN test.measurement m ON m.unit_id=ur.unit_id" in formal_sql


@pytest.mark.parametrize(
    ("field", "conflicting_value"),
    (
        ("step_code", "STEP_X_ALT"),
        ("sequence_no", 99),
        ("canonical_parameter_code", "PX_ALT"),
        ("condition_json", '{"text":"VGS=0V","bias1":"2V"}'),
    ),
)
def test_relationship_fails_closed_on_exact_identity_conflict_before_points(
    field: str, conflicting_value: object
) -> None:
    rows = _RelationshipConnection._identities()
    rows.extend(
        [
            {
                **rows[0],
                "run_id": 102,
                "run_program_version_id": 202,
                "program_version_id": 202,
                "test_item_id": 401,
                field: conflicting_value,
            },
            {
                **rows[1],
                "run_id": 102,
                "run_program_version_id": 202,
                "program_version_id": 202,
                "test_item_id": 402,
            },
        ]
    )
    connection = _RelationshipConnection(identity_rows=rows)

    with pytest.raises(DomainError) as error:
        SqlParameterRelationshipService(
            _Engine(connection)  # type: ignore[arg-type]
        ).relationship(_request())

    assert error.value.code == "ANALYSIS_PARAMETER_INCOMPATIBLE"
    assert all("REL_SCATTER" not in sql for sql, _ in connection.executed)


def test_relationship_fails_closed_for_mixed_dataset_stages() -> None:
    connection = _StageMismatchConnection()

    with pytest.raises(DomainError) as error:
        SqlParameterRelationshipService(
            _Engine(connection)  # type: ignore[arg-type]
        ).relationship(
            _request(
                datasets=[
                    {"dataset_id": 1, "version_no": 1},
                    {"dataset_id": 2, "version_no": 1},
                ]
            )
        )

    assert error.value.code == "ANALYSIS_STAGE_INCOMPATIBLE"
    assert all("REL_SOURCE" not in sql for sql, _ in connection.executed)


def test_relationship_rejects_one_test_item_id_claimed_by_multiple_programs() -> None:
    rows = _RelationshipConnection._identities()
    rows.extend(
        [
            {
                **rows[0],
                "run_id": 102,
                "run_program_version_id": 202,
                "program_version_id": 202,
            },
            {
                **rows[1],
                "run_id": 102,
                "run_program_version_id": 202,
                "program_version_id": 202,
                "test_item_id": 402,
            },
        ]
    )
    connection = _RelationshipConnection(identity_rows=rows)

    with pytest.raises(DomainError) as error:
        SqlParameterRelationshipService(
            _Engine(connection)  # type: ignore[arg-type]
        ).relationship(_request(analyses=["SCATTER"]))

    assert error.value.code == "ANALYSIS_PARAMETER_INCOMPATIBLE"
    assert all("REL_SCATTER" not in sql for sql, _ in connection.executed)


def test_multi_dataset_relationship_rejects_formal_operator_conflict() -> None:
    connection = _FormalOperatorMismatchConnection()

    with pytest.raises(DomainError) as error:
        SqlParameterRelationshipService(
            _Engine(connection)  # type: ignore[arg-type]
        ).relationship(
            _request(
                datasets=[
                    {"dataset_id": 1, "version_no": 1},
                    {"dataset_id": 2, "version_no": 1},
                ],
                analyses=["SCATTER"],
            )
        )

    assert error.value.code == "ANALYSIS_SPEC_INCOMPATIBLE"
    assert error.value.details == [{"parameters": ["PX"]}]
    assert all("REL_SCATTER" not in sql for sql, _ in connection.executed)


def test_multi_dataset_relationship_allows_different_spec_identity_with_same_semantics() -> (
    None
):
    connection = _FormalIdentityDifferenceConnection()

    result = SqlParameterRelationshipService(
        _Engine(connection)  # type: ignore[arg-type]
    ).relationship(
        _request(
            datasets=[
                {"dataset_id": 1, "version_no": 1},
                {"dataset_id": 2, "version_no": 1},
            ],
            analyses=["SCATTER"],
        )
    )

    assert result.rule_context.spec_versions == (
        "SPEC:7:FORMAL-V1",
        "SPEC:8:FORMAL-V2",
    )
    assert result.sampling_summary.original_points == 6


def test_eight_dataset_scatter_batches_read_only_work_without_changing_counts() -> None:
    connection = _EightDatasetBatchConnection(scatter_oos_count=0)

    result = SqlParameterRelationshipService(
        _Engine(connection)  # type: ignore[arg-type]
    ).relationship(
        _request(
            datasets=[
                {"dataset_id": dataset_id, "version_no": 1}
                for dataset_id in range(1, 9)
            ],
            analyses=["SCATTER"],
            group_by="DATASET",
        )
    )

    assert result.counts.input_units == result.counts.included_units == 24
    assert result.sampling_summary.original_points == 24
    assert result.sampling_summary.returned_points == 24
    assert {
        point.dataset_id for item in result.items for point in item.scatter_points
    } == set(range(1, 9))
    sql_text = [sql for sql, _ in connection.executed]
    assert sum("REL_MULTI_CONTEXT_IDENTITIES" in sql for sql in sql_text) == 1
    assert sum("REL_MULTI_COUNTS_DUPLICATE" in sql for sql in sql_text) == 1
    assert sum("REL_SCATTER_MULTI_COUNT" in sql for sql in sql_text) == 1
    assert sum("REL_SCATTER_MULTI_FETCH" in sql for sql in sql_text) == 1
    assert all("REL_SCATTER_SAMPLE" not in sql for sql in sql_text)
    preflight_sql = next(sql for sql in sql_text if "REL_SCATTER_MULTI_COUNT" in sql)
    fetch_sql = next(sql for sql in sql_text if "REL_SCATTER_MULTI_FETCH" in sql)
    assert "xi.program_version_id=tr.program_version_id" in preflight_sql
    assert "yi.program_version_id=tr.program_version_id" in preflight_sql
    assert "xi.program_version_id=tr.program_version_id" in fetch_sql
    assert "yi.program_version_id=tr.program_version_id" in fetch_sql
    assert "JOIN mdm.test_item_definition tx" not in fetch_sql
    assert "JOIN mdm.test_item_definition ty" not in fetch_sql
    assert "tr.metadata_json" not in fetch_sql
    assert "pv.version_code AS program_version" not in fetch_sql
    assert len(connection.executed) == 12


def test_identical_concurrent_eight_dataset_requests_share_only_inflight_read() -> None:
    connection = _BlockingEightDatasetBatchConnection()
    engine = _Engine(connection)
    request = _request(
        datasets=[
            {"dataset_id": dataset_id, "version_no": 1} for dataset_id in range(1, 9)
        ],
        analyses=["SCATTER"],
        group_by="DATASET",
    )
    start = Barrier(3)

    def invoke():
        start.wait()
        return SqlParameterRelationshipService(
            engine  # type: ignore[arg-type]
        ).relationship(request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(invoke)
        second = executor.submit(invoke)
        start.wait()
        assert connection.preflight_started.wait(timeout=5)
        connection.release_preflight.set()
        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert first_result == second_result
    assert (
        sum("REL_MULTI_CONTEXT_IDENTITIES" in sql for sql, _ in connection.executed)
        == 1
    )

    SqlParameterRelationshipService(
        engine  # type: ignore[arg-type]
    ).relationship(request)
    assert (
        sum("REL_MULTI_CONTEXT_IDENTITIES" in sql for sql, _ in connection.executed)
        == 2
    )


@pytest.mark.parametrize(
    ("scatter_count", "max_points"),
    ((3, 100), (85_624, 10_000)),
    ids=("single", "large-scatter"),
)
def test_identical_single_dataset_scatter_requests_share_only_inflight_read(
    monkeypatch,
    scatter_count: int,
    max_points: int,
) -> None:
    waiter_started = Event()
    monkeypatch.setattr(
        relationship_module,
        "Event",
        lambda: _TrackingRelationshipFlightEvent(waiter_started),
    )
    connection = _BlockingScatterConnection(scatter_count=scatter_count)
    engine = _Engine(connection)
    request = _request(
        analyses=["SCATTER"],
        group_by="DATASET",
        max_points=max_points,
    )
    start = Barrier(3)

    def invoke():
        start.wait()
        return SqlParameterRelationshipService(
            engine  # type: ignore[arg-type]
        ).relationship(request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(invoke)
        second = executor.submit(invoke)
        start.wait()
        assert connection.combined_started.wait(timeout=5)
        assert waiter_started.wait(timeout=5)
        connection.release_combined.set()
        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert first_result is second_result
    assert connection.combined_calls == 1
    assert first_result.sampling_summary.original_points == scatter_count
    assert first_result.sampling_summary.returned_points <= max_points

    later_result = SqlParameterRelationshipService(
        engine  # type: ignore[arg-type]
    ).relationship(request)

    assert later_result is not first_result
    assert connection.combined_calls == 2
    assert later_result.counts == first_result.counts
    assert later_result.sampling_summary == first_result.sampling_summary
    assert later_result.items == first_result.items


@pytest.mark.parametrize(
    "second_updates",
    (
        {"max_points": 101},
        {"filters": {"lot_ids": ["LOT-B"]}},
        {"datasets": [{"dataset_id": 1, "version_no": 2}]},
    ),
    ids=("max-points", "filter", "dataset-version"),
)
def test_different_scatter_requests_do_not_share_inflight_read(
    second_updates: dict[str, Any],
) -> None:
    connection = _BlockingScatterConnection(expected_combined_starts=2)
    engine = _Engine(connection)
    first_request = _request(analyses=["SCATTER"], group_by="DATASET", max_points=100)
    second_payload: dict[str, Any] = {
        "analyses": ["SCATTER"],
        "group_by": "DATASET",
        "max_points": 100,
    }
    second_payload.update(second_updates)
    second_request = _request(**second_payload)
    start = Barrier(3)

    def invoke(request):
        start.wait()
        return SqlParameterRelationshipService(
            engine  # type: ignore[arg-type]
        ).relationship(request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(invoke, first_request)
        second = executor.submit(invoke, second_request)
        start.wait()
        assert connection.combined_started.wait(timeout=5)
        connection.release_combined.set()
        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert connection.combined_calls == 2
    assert first_result.sampling_summary.original_points == 3
    assert second_result.sampling_summary.original_points == 3


def test_identical_scatter_requests_on_different_engines_do_not_share_flight() -> None:
    connections = (_BlockingScatterConnection(), _BlockingScatterConnection())
    engines = tuple(_Engine(connection) for connection in connections)
    request = _request(analyses=["SCATTER"], group_by="DATASET")
    start = Barrier(3)

    def invoke(engine):
        start.wait()
        return SqlParameterRelationshipService(
            engine  # type: ignore[arg-type]
        ).relationship(request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(invoke, engine) for engine in engines]
        start.wait()
        assert all(
            connection.combined_started.wait(timeout=5) for connection in connections
        )
        for connection in connections:
            connection.release_combined.set()
        results = [future.result(timeout=5) for future in futures]

    assert [connection.combined_calls for connection in connections] == [1, 1]
    assert results[0].items == results[1].items


def test_identical_scatter_requests_on_different_service_types_do_not_share_flight() -> (
    None
):
    class AlternateRelationshipService(SqlParameterRelationshipService):
        pass

    connection = _BlockingScatterConnection(expected_combined_starts=2)
    engine = _Engine(connection)
    request = _request(analyses=["SCATTER"], group_by="DATASET")
    start = Barrier(3)

    def invoke(service_type):
        start.wait()
        return service_type(engine).relationship(request)  # type: ignore[arg-type]

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(invoke, SqlParameterRelationshipService)
        second = executor.submit(invoke, AlternateRelationshipService)
        start.wait()
        assert connection.combined_started.wait(timeout=5)
        connection.release_combined.set()
        first.result(timeout=5)
        second.result(timeout=5)

    assert connection.combined_calls == 2


def test_scatter_singleflight_propagates_fresh_error_and_deletes_failed_entry(
    monkeypatch,
) -> None:
    waiter_started = Event()
    monkeypatch.setattr(
        relationship_module,
        "Event",
        lambda: _TrackingRelationshipFlightEvent(waiter_started, expected_waiters=2),
    )
    connection = _BlockingScatterConnection(fail_combined=True)
    engine = _Engine(connection)
    request = _request(analyses=["SCATTER"], group_by="DATASET")
    start = Barrier(4)

    def invoke():
        start.wait()
        return SqlParameterRelationshipService(
            engine  # type: ignore[arg-type]
        ).relationship(request)

    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(invoke)
        second = executor.submit(invoke)
        third = executor.submit(invoke)
        start.wait()
        assert connection.combined_started.wait(timeout=5)
        assert waiter_started.wait(timeout=5)
        connection.release_combined.set()
        first_error = first.exception(timeout=5)
        second_error = second.exception(timeout=5)
        third_error = third.exception(timeout=5)

    errors = (first_error, second_error, third_error)
    assert all(isinstance(error, DomainError) for error in errors)
    assert len({id(error) for error in errors}) == 3
    assert {error.code for error in errors} == {"ANALYSIS_TEST_FAILURE"}
    assert all(error.details == [{"phase": "scatter"}] for error in errors)
    assert len({id(error.details) for error in errors}) == 3
    assert all(error.__cause__ is None for error in errors)
    assert connection.combined_calls == 1

    connection.fail_combined = False
    result = SqlParameterRelationshipService(
        engine  # type: ignore[arg-type]
    ).relationship(request)

    assert result.sampling_summary.original_points == 3
    assert connection.combined_calls == 2


def test_relationship_flight_error_rebuilds_ordinary_and_interrupt_failures() -> None:
    original = ValueError("ordinary failure")

    with pytest.raises(ValueError, match="ordinary failure") as ordinary:
        relationship_module._raise_relationship_flight_error(original)

    assert ordinary.value is not original
    assert ordinary.value.__cause__ is None

    with pytest.raises(
        RuntimeError, match="coalesced relationship analysis was interrupted"
    ) as interrupted:
        relationship_module._raise_relationship_flight_error(KeyboardInterrupt())

    assert interrupted.value.__cause__ is None


def test_scatter_singleflight_excludes_trend_and_correlation_requests() -> None:
    eligible = SqlParameterRelationshipService._scatter_singleflight_eligible

    assert eligible(_request(analyses=["SCATTER"])) is True
    assert eligible(_request(analyses=["TREND"])) is False
    assert eligible(_request(analyses=["SCATTER", "TREND"])) is False
    assert (
        eligible(
            _request(
                analyses=["CORRELATION"],
                correlation={
                    "method": "PEARSON_PAIRWISE_V1",
                    "rule_code": "CORRELATION_RULE",
                    "version_code": "v1",
                },
            )
        )
        is False
    )


@pytest.mark.parametrize("analysis", ("TREND", "CORRELATION"))
def test_non_scatter_relationship_requests_execute_independently_under_concurrency(
    analysis: str,
) -> None:
    connection = _BlockingContextConnection()
    engine = _Engine(connection)
    correlation = (
        {
            "method": "PEARSON_PAIRWISE_V1",
            "rule_code": "CORRELATION_RULE",
            "version_code": "v1",
        }
        if analysis == "CORRELATION"
        else None
    )
    request = _request(
        analyses=[analysis],
        group_by="DATASET",
        **({"correlation": correlation} if correlation is not None else {}),
    )
    rules = _ApprovedCorrelationRuleService()
    start = Barrier(3)

    def invoke():
        start.wait()
        return SqlParameterRelationshipService(
            engine,  # type: ignore[arg-type]
            rule_service=rules,  # type: ignore[arg-type]
        ).relationship(request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(invoke)
        second = executor.submit(invoke)
        start.wait()
        assert connection.context_started.wait(timeout=5)
        connection.release_context.set()
        first.result(timeout=5)
        second.result(timeout=5)

    assert connection.context_calls == 2


@pytest.mark.parametrize(
    ("group_by", "expected_group"),
    (
        ("DATASET", "DATASET:1:V1"),
        ("TEST_BATCH", "TEST_BATCH:9001"),
        ("LOT", "LOT:LOT-A"),
        ("WAFER", "WAFER:LOT-A:W01"),
        ("SOURCE", "SOURCE:SOURCE-A"),
        ("TESTER", "TESTER:TESTER-A"),
        ("PROGRAM", "PROGRAM:PROGRAM-V1"),
        (
            "CONDITION",
            (
                'CONDITION:PX={"bias1":"1V","text":"VGS=0V"}'
                '|PY={"bias1":"2V","text":"VDS=5V"}'
            ),
        ),
    ),
)
def test_relationship_supports_each_grouping_dimension(
    group_by: str, expected_group: str
) -> None:
    result = SqlParameterRelationshipService(
        _Engine(_RelationshipConnection())  # type: ignore[arg-type]
    ).relationship(_request(analyses=["SCATTER"], group_by=group_by))

    assert {item.group_key for item in result.items} == {expected_group}


def test_source_identity_falls_back_to_run_not_tester() -> None:
    first = {"run_id": 101, "metadata_json": "{}", "tester_id": "TESTER-A"}
    second = {"run_id": 102, "metadata_json": "{}", "tester_id": "TESTER-A"}

    assert _source_identity(first) == "RUN-101"
    assert _source_identity(second) == "RUN-102"


def test_wafer_grouping_fails_closed_when_identity_is_unavailable() -> None:
    connection = _RelationshipConnection(
        scatter_oos_rows=[
            {
                **_point_row(3, x_value=11.0, y_value=6.0, x_status="OVER_RANGE"),
                "wafer_id": None,
            }
        ]
    )

    with pytest.raises(DomainError) as error:
        SqlParameterRelationshipService(
            _Engine(connection)  # type: ignore[arg-type]
        ).relationship(_request(analyses=["SCATTER"], group_by="WAFER"))

    assert error.value.code == "ANALYSIS_GROUP_DIMENSION_UNAVAILABLE"


def test_relationship_sampling_is_deterministic_and_preserves_all_oos() -> None:
    oos_rows = [
        _point_row(149, x_value=11.0, y_value=5.0, x_status="OVER_RANGE"),
        _point_row(150, x_value=12.0, y_value=6.0, x_status="OVER_RANGE"),
    ]
    connection = _RelationshipConnection(
        scatter_count=150,
        scatter_oos_count=2,
        scatter_oos_rows=oos_rows,
        scatter_sample_rows=[
            _point_row(1, x_value=1.0, y_value=2.0),
            _point_row(75, x_value=5.0, y_value=7.0),
        ],
    )

    result = SqlParameterRelationshipService(
        _Engine(connection)  # type: ignore[arg-type]
    ).relationship(_request(analyses=["SCATTER"]))

    assert result.sampling_summary.sampled is True
    assert result.sampling_summary.method == (
        "DETERMINISTIC_SCOPE_STRIDE_PRESERVE_FORMAL_SPEC_OOS_V2"
    )
    assert result.sampling_summary.original_points == 150
    assert result.sampling_summary.preserved_out_of_spec_points == 2
    assert {point.drilldown_key for point in result.items[0].scatter_points} >= {
        "UNIT:149",
        "UNIT:150",
    }
    assert result.sampling_summary.returned_points == 76
    combined_sql, combined_parameters = next(
        (sql, parameters)
        for sql, parameters in connection.executed
        if "REL_SCATTER_COMBINED" in sql
    )
    assert combined_parameters["combined_max_points"] == 100
    assert combined_parameters["combined_fetch_limit"] == 101
    assert "ROW_NUMBER() OVER(PARTITION BY is_out_of_spec" in combined_sql
    assert "candidate_count-out_of_spec_count" in combined_sql
    assert all("REL_SCATTER_OOS" not in sql for sql, _ in connection.executed)


def test_scatter_skips_oos_fetch_when_authoritative_count_is_zero() -> None:
    connection = _RelationshipConnection(
        scatter_count=3,
        scatter_oos_count=0,
        scatter_sample_rows=[
            _point_row(1, x_value=1.0, y_value=2.0),
            _point_row(2, x_value=2.0, y_value=4.0),
            _point_row(3, x_value=3.0, y_value=6.0),
        ],
    )

    result = SqlParameterRelationshipService(
        _Engine(connection)  # type: ignore[arg-type]
    ).relationship(_request(analyses=["SCATTER"]))

    assert result.sampling_summary.original_points == 3
    assert result.sampling_summary.returned_points == 3
    assert result.sampling_summary.preserved_out_of_spec_points == 0
    assert len(result.items[0].scatter_points) == 3
    assert any("REL_SCATTER_COMBINED" in sql for sql, _ in connection.executed)
    assert all("REL_SCATTER_COUNT" not in sql for sql, _ in connection.executed)
    assert all("REL_SCATTER_SAMPLE" not in sql for sql, _ in connection.executed)
    assert all("REL_SCATTER_OOS" not in sql for sql, _ in connection.executed)


def test_relationship_refuses_to_drop_oos_points_above_max_points() -> None:
    connection = _RelationshipConnection(
        scatter_count=150,
        scatter_oos_count=101,
        scatter_oos_rows=[
            _point_row(1, x_value=11.0, y_value=5.0, x_status="OVER_RANGE")
        ],
    )

    with pytest.raises(DomainError) as error:
        SqlParameterRelationshipService(
            _Engine(connection)  # type: ignore[arg-type]
        ).relationship(_request(analyses=["SCATTER"]))

    assert error.value.code == "ANALYSIS_RESULT_TOO_LARGE"
    assert all("REL_SCATTER_OOS" not in sql for sql, _ in connection.executed)


def test_relationship_returns_exactly_all_oos_when_oos_fills_max_points() -> None:
    oos_rows = [
        _point_row(
            unit_id,
            x_value=11.0,
            y_value=5.0,
            x_status="OVER_RANGE",
        )
        for unit_id in range(1, 101)
    ]
    connection = _RelationshipConnection(
        scatter_count=150,
        scatter_oos_count=100,
        scatter_oos_rows=oos_rows,
    )

    result = SqlParameterRelationshipService(
        _Engine(connection)  # type: ignore[arg-type]
    ).relationship(_request(analyses=["SCATTER"]))

    assert result.sampling_summary.original_points == 150
    assert result.sampling_summary.returned_points == 100
    assert result.sampling_summary.preserved_out_of_spec_points == 100
    combined_sql = next(
        sql for sql, _ in connection.executed if "REL_SCATTER_COMBINED" in sql
    )
    assert ":combined_max_points-out_of_spec_count>0" in combined_sql


def test_relationship_returns_no_spec_without_program_limit_fallback() -> None:
    connection = _RelationshipConnection(formal_spec_rows=[])

    result = SqlParameterRelationshipService(
        _Engine(connection)  # type: ignore[arg-type]
    ).relationship(_request(analyses=["SCATTER"]))

    assert result.rule_context.spec_versions == ()
    assert all(
        identity.formal_spec_status == "NO_SPEC"
        and identity.formal_lsl is None
        and identity.formal_usl is None
        for identity in result.items[0].identities
    )
    assert all(
        "FORMAL_RELEASED_SPEC_NOT_FOUND" in warning for warning in result.warnings
    )
    count_sql, count_parameters = next(
        (sql, parameters)
        for sql, parameters in connection.executed
        if "REL_SCATTER_COMBINED" in sql
    )
    assert "program_lsl" not in count_sql and "program_usl" not in count_sql
    assert count_parameters["x_lsl"] is None
    assert count_parameters["x_usl"] is None
    assert count_parameters["y_lsl"] is None
    assert count_parameters["y_usl"] is None


def test_correlation_requires_exact_rule_reference() -> None:
    with pytest.raises(ValidationError, match="exact rule version"):
        _request(analyses=["CORRELATION"], correlation={})


def test_correlation_registry_gate_checks_every_parameter_scope() -> None:
    connection = _RelationshipConnection()
    denied = _DeniedCorrelationRuleService()
    service = SqlParameterRelationshipService(
        _Engine(connection),
        rule_service=denied,  # type: ignore[arg-type]
    )

    with pytest.raises(DomainError) as error:
        service.relationship(
            _request(
                analyses=["CORRELATION"],
                correlation={
                    "method": "PEARSON_PAIRWISE_V1",
                    "rule_code": "CORRELATION_RULE",
                    "version_code": "v1",
                },
            )
        )

    assert error.value.code == "ANALYSIS_RULE_NOT_APPROVED"
    assert denied.calls == [
        {
            "rule_code": "CORRELATION_RULE",
            "version_code": "v1",
            "test_stage": "FT",
            "expected_algorithm_code": "PEARSON_PAIRWISE_V1",
            "supplier_id": 5,
            "product_id": 6,
            "parameter": "PX",
        }
    ]
    assert len(connection.executed) == 1


def test_approved_correlation_returns_versioned_pearson_result() -> None:
    connection = _RelationshipConnection()
    rules = _ApprovedCorrelationRuleService()
    service = SqlParameterRelationshipService(
        _Engine(connection),  # type: ignore[arg-type]
        rule_service=rules,  # type: ignore[arg-type]
    )

    result = service.relationship(
        _request(
            analyses=["CORRELATION"],
            correlation={
                "method": "PEARSON_PAIRWISE_V1",
                "rule_code": "CORRELATION_RULE",
                "version_code": "v1",
            },
        )
    )

    matrix = {
        (cell.x_parameter, cell.y_parameter): cell
        for cell in result.items[0].correlations
    }
    assert set(matrix) == {("PX", "PX"), ("PX", "PY"), ("PY", "PX"), ("PY", "PY")}
    assert matrix[("PX", "PY")].coefficient == pytest.approx(1.0)
    assert matrix[("PY", "PX")].coefficient == matrix[("PX", "PY")].coefficient
    assert all(cell.sample_count == 3 for cell in matrix.values())
    assert all(cell.status == "ELIGIBLE" for cell in matrix.values())
    assert all(cell.method == "PEARSON_PAIRWISE_V1" for cell in matrix.values())
    assert all(cell.rule_code == "CORRELATION_RULE:v1" for cell in matrix.values())
    assert [call["parameter"] for call in rules.calls] == ["PX", "PY"]
    assert all(call["supplier_id"] == 5 for call in rules.calls)
    assert all(call["product_id"] == 6 for call in rules.calls)


def test_trend_returns_stable_cross_run_and_dataset_ordinal() -> None:
    result = SqlParameterRelationshipService(
        _Engine(_OrderedTrendConnection())  # type: ignore[arg-type]
    ).relationship(
        _request(
            datasets=[
                {"dataset_id": 1, "version_no": 1},
                {"dataset_id": 2, "version_no": 1},
            ],
            analyses=["TREND"],
            group_by="LOT",
        )
    )

    points = sorted(
        (
            point
            for item in result.items
            for point in item.trend_points
            if point.parameter == "PX"
        ),
        key=lambda point: point.ordinal,
    )
    assert result.trend_order_basis == (
        "DATASET_ORDINAL_THEN_RUN_SOURCE_TIME_THEN_RUN_ID_"
        "THEN_UNIT_SEQUENCE_THEN_UNIT_ID"
    )
    assert [point.ordinal for point in points] == [1, 2, 3, 4, 5, 6]
    assert [point.dataset_id for point in points] == [1, 1, 1, 2, 2, 2]
    assert [point.run_id for point in points] == [101, 101, 102, 101, 101, 102]
    assert [point.source_sequence for point in points] == [1, 1, 1, 1, 1, 1]
    assert [point.drilldown_key for point in points[:3]] == [
        "UNIT:1",
        "UNIT:2",
        "UNIT:3",
    ]


def test_correlation_matrix_keeps_not_eligible_cells_with_pairwise_zero_count() -> None:
    result = SqlParameterRelationshipService(
        _Engine(_MissingPairCorrelationConnection()),  # type: ignore[arg-type]
        rule_service=_ApprovedCorrelationRuleService(),  # type: ignore[arg-type]
    ).relationship(
        _request(
            analyses=["CORRELATION"],
            correlation={
                "method": "PEARSON_PAIRWISE_V1",
                "rule_code": "CORRELATION_RULE",
                "version_code": "v1",
            },
        )
    )

    matrix = {
        (cell.x_parameter, cell.y_parameter): cell
        for cell in result.items[0].correlations
    }
    assert len(matrix) == 4
    missing = matrix[("PY", "PY")]
    assert missing.sample_count == 0
    assert missing.coefficient is None
    assert missing.status == "NOT_ELIGIBLE"
    assert missing.reason_code == "CORRELATION_INSUFFICIENT_PAIRS"
