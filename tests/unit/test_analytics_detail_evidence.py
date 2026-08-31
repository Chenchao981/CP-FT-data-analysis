from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain.analytics import AnalyticsDetailRequest
from app.infrastructure.sql_analytics_service import (
    SqlAnalyticsService,
    _formal_spec_from_evaluation_rows,
)
from app.main import create_app
from fastapi.testclient import TestClient


class _Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Result:
    def __init__(
        self, rows: list[dict[str, Any]] | None = None, scalar: int = 0
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> _Mappings:
        return _Mappings(self._rows)

    def scalar_one(self) -> int:
        return self._scalar


def _evaluation(evaluation_type: str, evaluation_id: int) -> dict[str, Any]:
    is_spec = evaluation_type == "SPEC"
    return {
        "evaluation_id": evaluation_id,
        "measurement_id": 7001,
        "evaluation_type": evaluation_type,
        "evaluation_scope_key": (
            "FORMAL_SPEC" if is_spec else f"DATASET:1:{evaluation_type}"
        ),
        "evaluation_result": "PASS",
        "evaluation_reason": None,
        "evaluation_run_id": 8000 + evaluation_id,
        "rule_code": f"{evaluation_type}_RULE",
        "rule_version_id": 9000 + evaluation_id,
        "rule_version": "v1",
        "spec_binding_id": 401 if is_spec else None,
        "binding_spec_set_id": 402 if is_spec else None,
        "spec_set_id": 402 if is_spec else None,
        "spec_version": "SPEC-V1" if is_spec else None,
        "spec_set_status": "RELEASED" if is_spec else None,
        "spec_item_id": 403 if is_spec else None,
        "item_spec_set_id": 402 if is_spec else None,
        "lsl_applied": 1.0 if is_spec else None,
        "usl_applied": 2.0 if is_spec else None,
        "lower_operator_applied": ">=" if is_spec else None,
        "upper_operator_applied": "<=" if is_spec else None,
        "processing_run_id": 901,
        "evaluated_at_utc": datetime(2026, 8, 31, tzinfo=UTC),
    }


class _Connection:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.parameters: list[Any] = []

    def execute(self, statement: Any, parameters: Any = None) -> _Result:
        sql = str(statement)
        self.sql.append(sql)
        self.parameters.append(parameters)
        if sql.startswith("SELECT COUNT_BIG(*)"):
            return _Result(scalar=1)
        if "ANALYTICS_DETAIL_SOURCE_LINEAGE" in sql:
            return _Result(
                rows=[
                    {
                        "processing_run_id": 901,
                        "source_file_id": 801,
                        "receipt_id": 701,
                        "original_file_name": "LOT-A.csv",
                        "sha256": "a" * 64,
                        "ordinal_no": 1,
                        "file_role": "DETAIL",
                        "lineage_basis": "WRITER_VERIFIED",
                    }
                ]
            )
        if "ANALYTICS_DETAIL_BIN_EVALUATIONS" in sql:
            return _Result(
                rows=[
                    {
                        "unit_bin_evaluation_id": 601,
                        "unit_id": 501,
                        "bin_type": "CP_BIN",
                        "raw_bin_code": "7",
                        "mapping_status": "MATCHED",
                        "bin_mapping_set_id": 51,
                        "mapping_version": "BIN-V1",
                        "bin_definition_id": 52,
                        "mapped_bin_name": "LEAKAGE",
                        "failure_mode_snapshot": "IDSS",
                        "is_pass_snapshot": False,
                        "processing_run_id": 901,
                        "evaluated_at_utc": datetime(2026, 8, 31, tzinfo=UTC),
                    }
                ]
            )
        if "ANALYTICS_DETAIL_MEASUREMENT_EVALUATIONS" in sql:
            return _Result(
                rows=[
                    _evaluation("SPEC", 7101),
                    _evaluation("PAT", 7102),
                    _evaluation("SBL", 7103),
                ]
            )
        if "ANALYTICS_DETAIL_MEASUREMENTS" in sql:
            return _Result(
                rows=[
                    {
                        "measurement_id": 7001,
                        "unit_id": 501,
                        "raw_item_name": "VTH",
                        "canonical_parameter_code": "VTH",
                        "step_code": "S1",
                        "sequence_no": 1,
                        "value_numeric": 1.5,
                        "value_text": None,
                        "measurement_status": "MEASURED",
                        "unit_code": "V",
                        "program_lsl": 0.5,
                        "program_usl": 2.5,
                    }
                ]
            )
        if "pr.processing_run_id" in sql and "sf.sha256" in sql:
            return _Result(
                rows=[
                    {
                        "unit_id": 501,
                        "logical_unit_key": "CP:LOT-A:W01:1:1",
                        "lot_id": "LOT-A",
                        "wafer_id": "W01",
                        "x_coord": 1,
                        "y_coord": 1,
                        "soft_bin": "7",
                        "hard_bin": None,
                        "overall_result": "FAIL",
                        "source_row_no": 51,
                        "run_id": 101,
                        "metadata_json": '{"source_id":"SRC-A"}',
                        "tester_id": "T-1",
                        "program_version": "PROGRAM-V1",
                        "cleaner_code": "CP",
                        "cleaner_version": "1.0",
                        "processing_run_id": 901,
                        "source_file_id": 801,
                        "sha256": "a" * 64,
                    }
                ]
            )
        raise AssertionError(sql)


class _Service(SqlAnalyticsService):
    @staticmethod
    def _source_rows(connection: Any, context: Any) -> tuple[Any, ...]:
        del connection, context
        return ()

    @staticmethod
    def _item_rows(connection: Any, context: Any) -> tuple[Any, ...]:
        del connection, context
        return ()

    @staticmethod
    def _selected_run_ids(request: Any, rows: Any) -> tuple[int, ...]:
        del request, rows
        return ()

    @staticmethod
    def _selected_condition_item_ids(request: Any, rows: Any) -> tuple[int, ...]:
        del request, rows
        return ()

    @staticmethod
    def _filter_sql(
        *args: Any, **kwargs: Any
    ) -> tuple[str, dict[str, Any], tuple[str, ...]]:
        del args, kwargs
        return "", {}, ()

    @staticmethod
    def _parameter_ids(rows: Any, parameters: Any) -> tuple[int, ...]:
        del rows, parameters
        return (301,)


def test_detail_returns_source_bin_and_every_current_measurement_evaluation() -> None:
    connection = _Connection()
    request = AnalyticsDetailRequest.model_validate(
        {
            "datasets": [{"dataset_id": 1, "version_no": 1}],
            "focus_dataset_id": 1,
            "parameters": ["VTH"],
        }
    )

    total, items = _Service(object())._detail_items(  # type: ignore[arg-type]
        connection, request, {"dataset_id": 1, "version_no": 1}
    )

    assert total == 1
    unit = items[0]
    assert unit.drilldown_key == "UNIT:501"
    assert (unit.source_file_id, unit.receipt_id, unit.original_file_name) == (
        801,
        701,
        "LOT-A.csv",
    )
    assert unit.sha256 == "a" * 64 and unit.source_row_no == 51
    assert unit.bin_evaluations[0].failure_mode_snapshot == "IDSS"
    measurement = unit.measurements[0]
    assert [item.evaluation_type for item in measurement.evaluations] == [
        "SPEC",
        "PAT",
        "SBL",
    ]
    assert measurement.formal_spec.status == "RESOLVED"
    assert measurement.formal_spec.spec_version == "SPEC-V1"
    assert (
        measurement.formal_spec.lsl_applied,
        measurement.formal_spec.usl_applied,
    ) == (1.0, 2.0)
    assert measurement.program_limit_source.endswith("NOT_FORMAL_SPEC")
    evaluation_sql = next(
        sql for sql in connection.sql if "MEASUREMENT_EVALUATIONS" in sql
    )
    assert "TOP (1)" not in evaluation_sql
    assert "ss.spec_set_id=si.spec_set_id" in evaluation_sql
    page_sql = next(
        sql for sql in connection.sql if "ur.unit_id,ur.logical_unit_key" in sql
    )
    assert "SELECT TOP (50)" in page_sql
    assert " OFFSET " not in page_sql
    assert any("SOURCE_LINEAGE_PRIMARY" in sql for sql in connection.sql)
    assert all("SOURCE_LINEAGE_FALLBACK" not in sql for sql in connection.sql)


def test_detail_later_page_keeps_exact_offset_paging_contract() -> None:
    connection = _Connection()
    request = AnalyticsDetailRequest.model_validate(
        {
            "datasets": [{"dataset_id": 1, "version_no": 1}],
            "focus_dataset_id": 1,
            "parameters": ["VTH"],
            "page": 2,
            "page_size": 50,
        }
    )

    _Service(object())._detail_items(  # type: ignore[arg-type]
        connection, request, {"dataset_id": 1, "version_no": 1}
    )

    page_index = next(
        index
        for index, sql in enumerate(connection.sql)
        if "ur.unit_id,ur.logical_unit_key" in sql
    )
    assert "TOP (50)" not in connection.sql[page_index]
    assert (
        "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY"
        in connection.sql[page_index]
    )
    assert connection.parameters[page_index]["offset"] == 50
    assert connection.parameters[page_index]["page_size"] == 50


class _FallbackLineageConnection(_Connection):
    def execute(self, statement: Any, parameters: Any = None) -> _Result:
        sql = str(statement)
        if "SOURCE_LINEAGE_PRIMARY" in sql:
            self.sql.append(sql)
            self.parameters.append(parameters)
            return _Result(rows=[])
        if "SOURCE_LINEAGE_FALLBACK" in sql:
            self.sql.append(sql)
            self.parameters.append(parameters)
            return _Result(
                rows=[
                    {
                        "processing_run_id": 901,
                        "source_file_id": 801,
                        "receipt_id": 701,
                        "original_file_name": "LOT-A.csv",
                        "sha256": "a" * 64,
                        "ordinal_no": 1,
                        "file_role": "DETAIL",
                        "lineage_basis": "PROCESSING_RUN_SOURCE",
                    }
                ]
            )
        return super().execute(statement, parameters)


def test_detail_source_lineage_fallback_runs_only_for_uncovered_processing_run() -> (
    None
):
    connection = _FallbackLineageConnection()
    request = AnalyticsDetailRequest.model_validate(
        {
            "datasets": [{"dataset_id": 1, "version_no": 1}],
            "focus_dataset_id": 1,
            "parameters": ["VTH"],
        }
    )

    _, items = _Service(object())._detail_items(  # type: ignore[arg-type]
        connection, request, {"dataset_id": 1, "version_no": 1}
    )

    assert [item.lineage_basis for item in items[0].source_files] == [
        "PROCESSING_RUN_SOURCE"
    ]
    fallback_index = next(
        index
        for index, sql in enumerate(connection.sql)
        if "SOURCE_LINEAGE_FALLBACK" in sql
    )
    assert connection.parameters[fallback_index]["fallback_run_ids"] == (901,)


def test_formal_spec_accepts_explicit_spec_item_provenance_without_fake_binding() -> (
    None
):
    evaluation = _evaluation("SPEC", 7101)
    evaluation["spec_binding_id"] = None
    evaluation["binding_spec_set_id"] = None

    formal = _formal_spec_from_evaluation_rows((evaluation,))

    assert formal.status == "RESOLVED"
    assert formal.spec_binding_id is None
    assert formal.spec_set_id == 402
    assert formal.spec_item_id == 403
    assert formal.spec_version == "SPEC-V1"
    assert (formal.lsl_applied, formal.usl_applied) == (1.0, 2.0)
    assert (formal.lower_operator_applied, formal.upper_operator_applied) == (
        ">=",
        "<=",
    )


def test_formal_spec_rejects_binding_and_item_spec_set_mismatch() -> None:
    evaluation = _evaluation("SPEC", 7101)
    evaluation["binding_spec_set_id"] = 999

    formal = _formal_spec_from_evaluation_rows((evaluation,))

    assert formal.status == "INVALID"
    assert formal.reason_code == "FORMAL_SPEC_PROVENANCE_INVALID"
    assert formal.spec_set_id is None


def test_formal_spec_rejects_missing_operator_for_an_applied_limit() -> None:
    evaluation = _evaluation("SPEC", 7101)
    evaluation["lower_operator_applied"] = None

    formal = _formal_spec_from_evaluation_rows((evaluation,))

    assert formal.status == "INVALID"
    assert formal.reason_code == "FORMAL_SPEC_PROVENANCE_INVALID"


def test_detail_evaluation_filter_is_an_exact_unit_exists_gate_only() -> None:
    connection = _Connection()
    request = AnalyticsDetailRequest.model_validate(
        {
            "datasets": [{"dataset_id": 1, "version_no": 1}],
            "focus_dataset_id": 1,
            "parameters": ["VTH"],
            "evaluation_filter": {
                "evaluation_type": "PAT",
                "evaluation_results": ["FAIL", "UNKNOWN"],
                "rule_code": "CP_PAT",
                "rule_version": "V2",
            },
        }
    )

    total, items = _Service(object())._detail_items(  # type: ignore[arg-type]
        connection, request, {"dataset_id": 1, "version_no": 1}
    )

    assert total == 1 and len(items) == 1
    count_sql = connection.sql[0]
    assert "EXISTS" in count_sql
    assert "risk_me.is_current=1" in count_sql
    assert "risk_me.evaluation_type" in count_sql
    assert "risk_me.evaluation_result IN" in count_sql
    assert "risk_rs.rule_code" in count_sql
    assert "risk_rv.version_code" in count_sql
    assert "risk_m.test_item_id IN" in count_sql
    assert connection.parameters[0]["detail_evaluation_type"] == "PAT"
    assert connection.parameters[0]["detail_evaluation_results"] == (
        "FAIL",
        "UNKNOWN",
    )
    assert connection.parameters[0]["detail_rule_code"] == "CP_PAT"
    assert connection.parameters[0]["detail_rule_version"] == "V2"
    evaluation_sql = next(
        sql
        for sql in connection.sql
        if "ANALYTICS_DETAIL_MEASUREMENT_EVALUATIONS" in sql
    )
    assert "risk_me.evaluation_result" not in evaluation_sql


def test_formal_spec_is_no_spec_without_current_released_spec_and_never_falls_back() -> (
    None
):
    formal = _formal_spec_from_evaluation_rows((_evaluation("PAT", 1),))
    assert formal.status == "NO_SPEC"
    assert formal.reason_code == "FORMAL_RELEASED_SPEC_NOT_FOUND"
    assert formal.lsl_applied is None and formal.usl_applied is None


@dataclass(frozen=True)
class _ApiDrilldownResult:
    unit: Any


class _ApiAnalyticsService:
    def __init__(self, unit: Any) -> None:
        self.unit = unit

    def drilldown(self, request: Any) -> _ApiDrilldownResult:
        del request
        return _ApiDrilldownResult(self.unit)


class _ApiDatasetService:
    def assert_dataset_access(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs


def test_drilldown_api_serializes_the_complete_evidence_contract() -> None:
    request = AnalyticsDetailRequest.model_validate(
        {
            "datasets": [{"dataset_id": 1, "version_no": 1}],
            "focus_dataset_id": 1,
            "parameters": ["VTH"],
        }
    )
    _, items = _Service(object())._detail_items(  # type: ignore[arg-type]
        _Connection(), request, {"dataset_id": 1, "version_no": 1}
    )
    app = create_app()
    app.state.dataset_service = _ApiDatasetService()
    app.state.analytics_service = _ApiAnalyticsService(items[0])

    response = TestClient(app).post(
        "/api/v1/analytics/drilldown",
        json={
            "datasets": [{"dataset_id": 1, "version_no": 1}],
            "parameters": ["VTH"],
            "drilldown_key": "UNIT:501",
        },
    )

    assert response.status_code == 200
    unit = response.json()["unit"]
    assert unit["source_file_id"] == 801
    assert unit["source_files"][0]["receipt_id"] == 701
    assert unit["bin_evaluations"][0]["mapping_version"] == "BIN-V1"
    assert [
        item["evaluation_type"] for item in unit["measurements"][0]["evaluations"]
    ] == ["SPEC", "PAT", "SBL"]
    assert unit["measurements"][0]["formal_spec"]["status"] == "RESOLVED"
