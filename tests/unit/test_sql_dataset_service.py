from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from app.infrastructure.sql_dataset_service import SqlDatasetService


class Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def one_or_none(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class Result:
    def __init__(self, *, rows=None, scalar=None) -> None:
        self.rows = rows or []
        self.scalar = scalar

    def mappings(self):
        return Mappings(self.rows)

    def scalar_one(self):
        return self.scalar


class GateConnection:
    def __init__(self, *, run_status="READY", lineage=True, blocking=0) -> None:
        self.run_status = run_status
        self.lineage = lineage
        self.blocking = blocking

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "FROM dataset.dataset_version dv" in sql and "d.supplier_id" in sql:
            return Result(
                rows=[
                    {
                        "dataset_version_id": 5,
                        "dataset_id": 1,
                        "version_no": 1,
                        "input_batch_id": 8,
                        "canonical_model_version": "1.0",
                        "status": "VALIDATING",
                        "is_current": False,
                        "supplier_id": 2,
                        "product_id": 3,
                        "test_stage": "CP",
                    }
                ]
            )
        if "CASE WHEN pj.import_batch_id" in sql:
            return Result(
                rows=[
                    {
                        "processing_run_id": 7,
                        "source_file_id": 11,
                        "status": self.run_status,
                        "unit_count": 10,
                        "measurement_count": 110,
                        "lineage_matches": self.lineage,
                    }
                ]
            )
        if "duplicates" in sql:
            return Result(scalar=0)
        if "NOT EXISTS(SELECT 1 FROM test.test_run" in sql:
            return Result(scalar=0)
        if "JOIN ingestion.data_quality_issue" in sql:
            return Result(scalar=self.blocking)
        raise AssertionError(sql)


class Engine:
    def __init__(self, connection) -> None:
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection


class FtChartConnection:
    def execute(self, statement, parameters=None):
        sql = str(statement)
        parameters = parameters or {}
        if "SELECT DISTINCT tr.run_id" in sql:
            return Result(
                rows=[
                    {
                        "run_id": 101,
                        "lot_id": "LOT-1",
                        "tester_id": "NCT-1",
                        "program_version_id": 201,
                        "metadata_json": '{"source_id":"SOURCE-RUN-A"}',
                    },
                    {
                        "run_id": 102,
                        "lot_id": "LOT-1",
                        "tester_id": "NCT-1",
                        "program_version_id": 202,
                        "metadata_json": '{"source_id":"SOURCE-RUN-B"}',
                    },
                ]
            )
        if "SELECT DISTINCT tid.sequence_no" in sql:
            rows = [
                {
                    "sequence_no": 1,
                    "raw_item_name": "HVBCES",
                    "unit_code": "kV",
                    "lsl": 1.29,
                    "usl": None,
                    "condition_json": '{"text":"VGE=0V"}',
                },
                {
                    "sequence_no": 1,
                    "raw_item_name": "HVBCES",
                    "unit_code": "kV",
                    "lsl": 1.27,
                    "usl": None,
                    "condition_json": '{"text":"VGE=0V"}',
                },
            ]
            if parameters.get("source_run_ids") == (101,):
                rows = rows[:1]
            return Result(rows=rows)
        if "SELECT COUNT_BIG(*)" in sql:
            assert parameters["source_run_ids"] == (101,)
            return Result(scalar=3)
        if ";WITH points AS" in sql:
            assert parameters["source_run_ids"] == (101,)
            return Result(
                rows=[
                    {
                        "run_id": 101,
                        "unit_sequence": 1,
                        "lot_id": "LOT-1",
                        "value_numeric": 1.31,
                        "measurement_status": "IN_SPEC",
                    }
                ]
            )
        raise AssertionError(sql)


def test_sql_dq_gate_passes_only_ready_attributable_clean_data() -> None:
    result = SqlDatasetService(Engine(GateConnection())).evaluate_gate(1, 1)  # type: ignore[arg-type]
    assert result.status == "PASS"
    assert result.run_count == 1
    assert result.unit_count == 10
    assert result.measurement_count == 110


def test_sql_dq_gate_reports_each_blocking_dimension() -> None:
    result = SqlDatasetService(
        Engine(GateConnection(run_status="FAILED", lineage=False, blocking=2))  # type: ignore[arg-type]
    ).evaluate_gate(1, 1)
    assert result.status == "BLOCKED"
    assert {reason.code for reason in result.reasons} == {
        "RUN_NOT_READY",
        "INPUT_LINEAGE_MISMATCH",
        "BLOCKING_DQ_ISSUE",
    }


def test_ft_charts_keep_source_file_runs_distinct_from_physical_tester() -> None:
    service = SqlDatasetService(Engine(FtChartConnection()))  # type: ignore[arg-type]
    result = service._get_ft_chart_data(
        FtChartConnection(),  # type: ignore[arg-type]
        {
            "dataset_id": 1,
            "version_no": 1,
            "test_stage": "FT",
            "product_name": "PRODUCT-1",
            "spec_set_id": None,
        },
        {
            "dataset_id": 1,
            "version_no": 1,
            "lot_id": None,
            "wafer_id": None,
            "source_id": None,
            "parameter": None,
        },
        " FROM dataset.dataset_version dv JOIN dataset.dataset_version_run dvr "
        "ON dvr.dataset_version_id=dv.dataset_version_id JOIN test.test_run tr "
        "ON tr.processing_run_id=dvr.processing_run_id ",
    )

    assert result.source_options == ("SOURCE-RUN-A", "SOURCE-RUN-B")
    assert len(result.parameter_options) == 1
    assert result.parameter_options[0].lsl is None


def test_ft_charts_resolve_the_selected_source_run_spec_and_points() -> None:
    service = SqlDatasetService(Engine(FtChartConnection()))  # type: ignore[arg-type]
    result = service._get_ft_chart_data(
        FtChartConnection(),  # type: ignore[arg-type]
        {
            "dataset_id": 1,
            "version_no": 1,
            "test_stage": "FT",
            "product_name": "PRODUCT-1",
            "spec_set_id": None,
        },
        {
            "dataset_id": 1,
            "version_no": 1,
            "lot_id": None,
            "wafer_id": None,
            "source_id": "SOURCE-RUN-A",
            "parameter": "HVBCES",
        },
        " FROM dataset.dataset_version dv JOIN dataset.dataset_version_run dvr "
        "ON dvr.dataset_version_id=dv.dataset_version_id JOIN test.test_run tr "
        "ON tr.processing_run_id=dvr.processing_run_id ",
    )

    assert result.parameter_options[0].lsl == 1.29
    assert result.ft_total_point_count == 3
    assert result.ft_parameter_points[0].source_id == "SOURCE-RUN-A"
