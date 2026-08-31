from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from app.domain.analytics import AnalyticsOverviewRequest
from app.infrastructure import sql_analytics_service as analytics_module
from app.infrastructure.sql_analytics_service import SqlAnalyticsService


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self):
        return self._rows

    def one(self):
        if len(self._rows) != 1:
            raise AssertionError(f"expected one row, received {len(self._rows)}")
        return self._rows[0]


class _Result:
    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        scalar: int | None = None,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return _Mappings(self._rows)

    def scalar_one(self):
        if self._scalar is None:
            raise AssertionError("expected scalar result")
        return self._scalar


class _OverviewConnection:
    def __init__(self, mapped_dataset_ids: set[int]) -> None:
        self._mapped_dataset_ids = mapped_dataset_ids
        self.grouped_bin_dataset_ids: list[int] = []
        self.batch_trend_statement_count = 0
        self.batch_evaluation_statement_count = 0
        self.batch_evaluation_parameters: dict[str, object] = {}
        self.option_statement_count = 0
        self.batch_shell_scope_statement_count = 0
        self.batch_shell_aggregate_statement_count = 0
        self.batch_shell_evaluation_statement_count = 0

    def execute(self, statement, parameters=None):
        sql = str(statement)
        values = dict(parameters or {})
        dataset_id = int(values["dataset"]) if "dataset" in values else None
        requested_dataset_ids = tuple(
            int(values[name])
            for name in sorted(values)
            if name.startswith("overview_dataset_")
        )
        if "SELECT DISTINCT bms.bin_mapping_set_id,bms.version_code" in sql:
            rows = (
                [{"bin_mapping_set_id": 51, "version_code": "BIN-V1"}]
                if dataset_id in self._mapped_dataset_ids
                else []
            )
            return _Result(rows=rows)
        if "OUTER APPLY" in sql:
            mapped = tuple(
                item
                for item in requested_dataset_ids
                if item in self._mapped_dataset_ids
            )
            self.grouped_bin_dataset_ids.extend(mapped)
            return _Result(
                rows=[
                    {
                        "dataset_id": item,
                        "version_no": 1,
                        "bin_mapping_set_id": 51,
                        "bin_definition_id": 501,
                        "mapping_version": "BIN-V1",
                        "bin_type": "SOFT_BIN",
                        "bin_code": "1",
                        "bin_name": "PASS",
                        "failure_mode": None,
                        "is_pass": True,
                        "unit_count": 2,
                        "drilldown_unit_id": item * 100,
                    }
                    for item in mapped
                ]
            )
        if sql.startswith("SELECT COUNT_BIG(*) FROM dataset.dataset_version"):
            return _Result(scalar=2)
        if "SELECT DISTINCT tr.lot_id" in sql:
            self.option_statement_count += 1
            return _Result(
                rows=[
                    {
                        "lot_id": f"LOT-{dataset_id}",
                        "wafer_id": None,
                        "bin_code": bin_code,
                    }
                    for bin_code in ("1", "7")
                ]
            )
        if "FROM mdm.test_item_definition tid" in sql:
            program_version_ids = values.get(
                "evaluation_program_version_ids",
                values.get("shell_program_version_ids", ()),
            )
            return _Result(
                rows=[
                    {"test_item_id": int(program_version_id) * 10}
                    for program_version_id in program_version_ids
                ]
            )
        if "SELECT requested.dataset_id,requested.version_no,tr.lot_id" in sql:
            self.batch_shell_scope_statement_count += 1
            return _Result(
                rows=[
                    {
                        "dataset_id": item,
                        "version_no": 1,
                        "lot_id": f"LOT-{item}",
                        "wafer_id": None,
                        "bin_code": "1" if offset == 0 else "7",
                        "unit_count": 1,
                        "pass_count": 1 if offset == 0 else 0,
                        "fail_count": 0 if offset == 0 else 1,
                        "unknown_count": 0,
                        "abort_count": 0,
                    }
                    for item in requested_dataset_ids
                    for offset in (0, 1)
                ]
            )
        if (
            "SELECT requested.dataset_id,requested.version_no,COUNT_BIG(*) "
            "AS unit_count" in sql
        ):
            self.batch_shell_aggregate_statement_count += 1
            return _Result(
                rows=[
                    {
                        "dataset_id": item,
                        "version_no": 1,
                        "unit_count": 1,
                        "pass_count": 1,
                        "fail_count": 0,
                        "unknown_count": 0,
                        "abort_count": 0,
                    }
                    for item in requested_dataset_ids
                ]
            )
        if "tr.run_id,tr.started_at_utc,tr.lot_id" in sql:
            self.batch_trend_statement_count += 1
            filtered = "ur.overall_result IN" in sql
            return _Result(
                rows=[
                    {
                        "dataset_id": item,
                        "version_no": 1,
                        "run_id": item * 10,
                        "started_at_utc": None,
                        "lot_id": f"LOT-{item}",
                        "wafer_id": None,
                        "drilldown_unit_id": item * 100,
                        "unit_count": 1,
                        "pass_count": 1,
                        "fail_count": 0,
                        "unknown_count": 0,
                        "abort_count": 0,
                        "option_bin_code": "1",
                    }
                    for item in requested_dataset_ids
                ]
                if filtered
                else [
                    {
                        "dataset_id": item,
                        "version_no": 1,
                        "run_id": item * 10,
                        "started_at_utc": None,
                        "lot_id": f"LOT-{item}",
                        "wafer_id": None,
                        "drilldown_unit_id": item * 100 + offset,
                        "unit_count": 1,
                        "pass_count": 1 if offset == 0 else 0,
                        "fail_count": 0 if offset == 0 else 1,
                        "unknown_count": 0,
                        "abort_count": 0,
                        "option_bin_code": "1" if offset == 0 else "7",
                    }
                    for item in requested_dataset_ids
                    for offset in (0, 1)
                ]
            )
        if "me.evaluation_type" in sql:
            if "shell_run_ids" in values:
                self.batch_shell_evaluation_statement_count += 1
            else:
                self.batch_evaluation_statement_count += 1
                self.batch_evaluation_parameters = values
            return _Result(rows=[])
        raise AssertionError(sql)


class _Engine:
    def __init__(self, connection: _OverviewConnection) -> None:
        self._connection = connection

    @contextmanager
    def connect(self):
        yield self._connection


def _context(dataset_ids: tuple[int, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "dataset_version_id": dataset_id * 10,
            "dataset_id": dataset_id,
            "version_no": 1,
            "input_batch_id": dataset_id * 1000,
            "status": "PUBLISHED",
            "is_current": True,
            "spec_set_id": None,
            "dataset_name": f"FT Dataset {dataset_id}",
            "test_stage": "FT",
            "supplier_id": 5,
            "product_id": 6,
            "product_name": "PRODUCT-A",
            "spec_version": None,
        }
        for dataset_id in dataset_ids
    )


def _request(
    dataset_ids: tuple[int, ...], *, overall_results: tuple[str, ...] = ()
) -> AnalyticsOverviewRequest:
    return AnalyticsOverviewRequest.model_validate(
        {
            "datasets": [
                {"dataset_id": dataset_id, "version_no": 1}
                for dataset_id in dataset_ids
            ],
            "focus_dataset_id": dataset_ids[0],
            "filters": {"overall_results": list(overall_results)},
        }
    )


def _run_overview(
    monkeypatch, *, mapped_dataset_ids: set[int], dataset_ids: tuple[int, ...]
):
    contexts = _context(dataset_ids)
    connection = _OverviewConnection(mapped_dataset_ids)
    service = SqlAnalyticsService(_Engine(connection))  # type: ignore[arg-type]
    monkeypatch.setattr(
        service, "_context_rows", lambda _connection, _request: contexts
    )
    monkeypatch.setattr(
        service,
        "_source_rows",
        lambda _connection, context: (
            {
                "run_id": int(context["dataset_id"]) * 10,
                "metadata_json": f'{{"source_id":"SOURCE-{context["dataset_id"]}"}}',
                "tester_id": "TESTER-A",
                "program_version_id": int(context["dataset_id"]) * 100,
                "program_version": "PROGRAM-V1",
            },
        ),
    )
    monkeypatch.setattr(
        service,
        "_item_rows",
        lambda _connection, context: (
            {
                "test_item_id": int(context["dataset_id"]) * 1000,
                "raw_item_name": "PARAM-A",
                "condition_json": None,
            },
        ),
    )
    monkeypatch.setattr(
        analytics_module,
        "resolve_formal_spec_context",
        lambda _connection, _contexts, _request: SimpleNamespace(spec_versions=()),
    )
    return service.overview(_request(dataset_ids)), connection


def _run_shell_context(
    monkeypatch,
    *,
    dataset_ids: tuple[int, ...],
    overall_results: tuple[str, ...] = (),
):
    contexts = _context(dataset_ids)
    connection = _OverviewConnection(set())
    service = SqlAnalyticsService(_Engine(connection))  # type: ignore[arg-type]
    monkeypatch.setattr(
        service, "_context_rows", lambda _connection, _request: contexts
    )
    monkeypatch.setattr(
        service,
        "_source_rows",
        lambda _connection, context: (
            {
                "run_id": int(context["dataset_id"]) * 10,
                "metadata_json": f'{{"source_id":"SOURCE-{context["dataset_id"]}"}}',
                "tester_id": "TESTER-A",
                "program_version_id": int(context["dataset_id"]) * 100,
                "program_version": "PROGRAM-V1",
            },
        ),
    )
    monkeypatch.setattr(
        service,
        "_item_rows",
        lambda _connection, context: (
            {
                "test_item_id": int(context["dataset_id"]) * 1000,
                "raw_item_name": "PARAM-A",
                "condition_json": None,
            },
        ),
    )
    monkeypatch.setattr(
        analytics_module,
        "resolve_formal_spec_context",
        lambda _connection, _contexts, _request: SimpleNamespace(spec_versions=()),
    )
    return (
        service.shell_context(_request(dataset_ids, overall_results=overall_results)),
        connection,
    )


def test_overview_skips_grouped_bin_sql_for_single_unmapped_dataset(
    monkeypatch,
) -> None:
    result, connection = _run_overview(
        monkeypatch, mapped_dataset_ids=set(), dataset_ids=(1,)
    )

    capability = next(item for item in result.capabilities if item.code == "BIN_PARETO")
    assert connection.grouped_bin_dataset_ids == []
    assert connection.batch_trend_statement_count == 1
    assert connection.batch_evaluation_statement_count == 1
    assert connection.option_statement_count == 0
    assert connection.batch_evaluation_parameters["evaluation_scope_run_ids"] == (10,)
    assert connection.batch_evaluation_parameters["evaluation_scope_item_ids"] == (
        1000,
    )
    assert result.counts.included_units == 2
    assert result.counts.pass_count == 1
    assert result.counts.fail_count == 1
    assert result.counts.yield_rate == 0.5
    assert len(result.yield_trend) == 1
    assert result.yield_trend[0].unit_count == 2
    assert result.yield_trend[0].drilldown_key == "UNIT:100"
    assert result.options.bin_codes == ("1", "7")
    assert result.rule_context.bin_mapping_versions == ()
    assert result.bin_pareto == ()
    assert capability.status == "UNAVAILABLE"
    assert capability.reason_code == "ANALYSIS_BIN_MAPPING_REQUIRED"
    assert "BIN_MAPPING_INCOMPLETE_OR_AMBIGUOUS" in result.warnings


def test_overview_mixed_mapping_queries_only_mapped_dataset_and_fails_closed(
    monkeypatch,
) -> None:
    result, connection = _run_overview(
        monkeypatch, mapped_dataset_ids={1}, dataset_ids=(1, 2)
    )

    capability = next(item for item in result.capabilities if item.code == "BIN_PARETO")
    assert connection.grouped_bin_dataset_ids == [1]
    assert connection.batch_trend_statement_count == 1
    assert connection.batch_evaluation_statement_count == 1
    assert connection.option_statement_count == 0
    assert result.counts.included_units == 4
    assert len(result.yield_trend) == 2
    assert result.rule_context.bin_mapping_versions == ("BIN:51:BIN-V1",)
    assert result.bin_pareto == ()
    assert capability.status == "UNAVAILABLE"
    assert capability.reason_code == "ANALYSIS_BIN_MAPPING_REQUIRED"
    assert "BIN_MAPPING_INCOMPLETE_OR_AMBIGUOUS" in result.warnings


def test_overview_filtered_path_preserves_unfiltered_options_and_input_count(
    monkeypatch,
) -> None:
    contexts = _context((1,))
    connection = _OverviewConnection(set())
    service = SqlAnalyticsService(_Engine(connection))  # type: ignore[arg-type]
    monkeypatch.setattr(
        service, "_context_rows", lambda _connection, _request: contexts
    )
    monkeypatch.setattr(
        service,
        "_source_rows",
        lambda _connection, _context: (
            {
                "run_id": 10,
                "metadata_json": '{"source_id":"SOURCE-1"}',
                "tester_id": "TESTER-A",
                "program_version_id": 100,
                "program_version": "PROGRAM-V1",
            },
        ),
    )
    monkeypatch.setattr(
        service,
        "_item_rows",
        lambda _connection, _context: (
            {
                "test_item_id": 1000,
                "raw_item_name": "PARAM-A",
                "condition_json": None,
            },
        ),
    )
    monkeypatch.setattr(
        analytics_module,
        "resolve_formal_spec_context",
        lambda _connection, _contexts, _request: SimpleNamespace(spec_versions=()),
    )

    result = service.overview(_request((1,), overall_results=("PASS",)))

    assert connection.option_statement_count == 1
    assert result.counts.input_units == 2
    assert result.counts.included_units == 1
    assert result.counts.excluded_units == 1
    assert result.counts.pass_count == 1
    assert result.counts.fail_count == 0
    assert result.options.bin_codes == ("1", "7")
    assert len(result.yield_trend) == 1


def test_shell_context_batches_eight_dataset_counts_options_and_evaluations(
    monkeypatch,
) -> None:
    result, connection = _run_shell_context(
        monkeypatch, dataset_ids=(1, 2, 3, 4, 5, 6, 7, 8)
    )

    assert connection.batch_shell_scope_statement_count == 1
    assert connection.batch_shell_aggregate_statement_count == 0
    assert connection.batch_shell_evaluation_statement_count == 1
    assert connection.option_statement_count == 0
    assert result.counts.input_units == 16
    assert result.counts.included_units == 16
    assert result.counts.pass_count == 8
    assert result.counts.fail_count == 8
    assert result.counts.yield_rate == 0.5
    assert result.options.bin_codes == ("1", "7")
    assert result.options.lot_ids == tuple(f"LOT-{item}" for item in range(1, 9))


def test_shell_context_filtered_batch_keeps_unfiltered_input_and_options(
    monkeypatch,
) -> None:
    result, connection = _run_shell_context(
        monkeypatch,
        dataset_ids=(1, 2),
        overall_results=("PASS",),
    )

    assert connection.batch_shell_scope_statement_count == 1
    assert connection.batch_shell_aggregate_statement_count == 1
    assert connection.batch_shell_evaluation_statement_count == 1
    assert result.counts.input_units == 4
    assert result.counts.included_units == 2
    assert result.counts.excluded_units == 2
    assert result.counts.pass_count == 2
    assert result.counts.fail_count == 0
    assert result.options.bin_codes == ("1", "7")
