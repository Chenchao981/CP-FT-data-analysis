from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.datasets import DatasetComparisonRequest
from app.infrastructure.sql_dataset_service import SqlDatasetService

_SYSTEM_ADMIN = Principal(
    1,
    "system.admin",
    "System Admin",
    ("SYSTEM_ADMIN",),
    frozenset({"DATASET_READ"}),
)


class Mappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def one_or_none(self):
        return self.rows[0] if self.rows else None

    def one(self):
        if len(self.rows) != 1:
            raise AssertionError(f"expected one row, received {len(self.rows)}")
        return self.rows[0]

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

    def scalar_one_or_none(self):
        return self.scalar

    def all(self):
        return self.rows


class GateConnection:
    def __init__(
        self,
        *,
        run_status="READY",
        lineage=True,
        blocking=0,
        version_status="VALIDATING",
        is_current=False,
        owner_user_id=7,
    ) -> None:
        self.run_status = run_status
        self.lineage = lineage
        self.blocking = blocking
        self.version_status = version_status
        self.is_current = is_current
        self.owner_user_id = owner_user_id

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "FROM dataset.dataset_version dv" in sql and "d.supplier_id" in sql:
            parameters = parameters or {}
            if "iam.data_scope_grant" in sql and not (
                bool(parameters.get("is_admin"))
                or parameters.get("user_id") == self.owner_user_id
                or (self.version_status == "PUBLISHED" and self.is_current)
            ):
                return Result(rows=[])
            return Result(
                rows=[
                    {
                        "dataset_version_id": 5,
                        "dataset_id": 1,
                        "version_no": 1,
                        "input_batch_id": 8,
                        "canonical_model_version": "1.0",
                        "status": self.version_status,
                        "is_current": self.is_current,
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

    @contextmanager
    def begin(self):
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


class FtMultiLotSpecConnection:
    def execute(self, statement, parameters=None):
        sql = str(statement)
        parameters = parameters or {}
        if "SELECT DISTINCT tr.run_id" in sql:
            rows = [
                {
                    "run_id": 101,
                    "lot_id": "LOT-1",
                    "tester_id": "NCT-1",
                    "program_version_id": 201,
                    "metadata_json": '{"source_id":"SOURCE-RUN-A"}',
                },
                {
                    "run_id": 102,
                    "lot_id": "LOT-2",
                    "tester_id": "NCT-2",
                    "program_version_id": 202,
                    "metadata_json": '{"source_id":"SOURCE-RUN-B"}',
                },
            ]
            if (
                "(:lot_id IS NULL OR tr.lot_id=:lot_id)" in sql
                and parameters.get("lot_id")
            ):
                rows = [row for row in rows if row["lot_id"] == parameters["lot_id"]]
            return Result(rows=rows)
        if "SELECT DISTINCT tid.sequence_no" in sql:
            rows = [
                {
                    "sequence_no": 1,
                    "raw_item_name": "HVBCES",
                    "unit_code": "kV",
                    "lsl": 1.29,
                    "usl": None,
                    "condition_json": '{"text":"LOT-1 condition"}',
                    "lot_id": "LOT-1",
                },
                {
                    "sequence_no": 1,
                    "raw_item_name": "HVBCES",
                    "unit_code": "kV",
                    "lsl": 1.17,
                    "usl": None,
                    "condition_json": '{"text":"LOT-2 condition"}',
                    "lot_id": "LOT-2",
                },
            ]
            if "(:lot_id IS NULL OR tr.lot_id=:lot_id)" in sql:
                rows = [row for row in rows if row["lot_id"] == parameters["lot_id"]]
            return Result(rows=rows)
        raise AssertionError(sql)


class CpChartConnection:
    def __init__(
        self,
        *,
        unit_count: int,
        pass_count: int,
        fail_count: int,
        unknown_count: int,
        abort_count: int,
        status: str = "PUBLISHED",
        is_current: bool = True,
    ) -> None:
        self.yield_row = {
            "lot_id": "LOT-CP",
            "wafer_id": "01",
            "unit_count": unit_count,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "unknown_count": unknown_count,
            "abort_count": abort_count,
        }
        self.status = status
        self.is_current = is_current

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "d.supplier_id" in sql and "dv.spec_set_id" in sql:
            return Result(
                rows=[
                    {
                        "dataset_version_id": 5,
                        "dataset_id": 1,
                        "version_no": 1,
                        "input_batch_id": 8,
                        "canonical_model_version": "1.0",
                        "status": self.status,
                        "is_current": self.is_current,
                        "supplier_id": 2,
                        "product_id": 3,
                        "test_stage": "CP",
                        "product_name": "PRODUCT-CP",
                        "spec_set_id": 4,
                    }
                ]
            )
        if "SELECT DISTINCT tr.lot_id,tr.wafer_id" in sql:
            return Result(rows=[{"lot_id": "LOT-CP", "wafer_id": "01"}])
        if "AS pass_count" in sql and "GROUP BY tr.lot_id,tr.wafer_id" in sql:
            for status in ("PASS", "FAIL", "UNKNOWN", "ABORT"):
                assert f"ur.overall_result='{status}'" in sql
            return Result(rows=[self.yield_row])
        if "GROUP BY ur.soft_bin" in sql:
            return Result(
                rows=[{"soft_bin": "1", "unit_count": self.yield_row["unit_count"]}]
            )
        raise AssertionError(sql)


class SummaryConnection:
    def __init__(
        self,
        *,
        units: int,
        passes: int,
        failures: int,
        version_status: str = "PUBLISHED",
        is_current: bool = True,
        owner_user_id: int = 7,
    ) -> None:
        self.units = units
        self.passes = passes
        self.failures = failures
        self.version_status = version_status
        self.is_current = is_current
        self.owner_user_id = owner_user_id

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "COUNT(DISTINCT dvr.processing_run_id)" in sql:
            parameters = parameters or {}
            if "iam.data_scope_grant" in sql and not (
                bool(parameters.get("is_admin"))
                or parameters.get("user_id") == self.owner_user_id
                or (self.version_status == "PUBLISHED" and self.is_current)
            ):
                return Result(rows=[])
            return Result(
                rows=[
                    {
                        "dataset_code": "FT-1",
                        "dataset_name": "FT dataset",
                        "status": self.version_status,
                        "is_current": self.is_current,
                        "run_count": 1,
                        "lot_count": 1,
                        "wafer_count": 0,
                        "unit_count": self.units,
                        "pass_count": self.passes,
                        "fail_count": self.failures,
                    }
                ]
            )
        if "JOIN test.measurement m" in sql and "COUNT_BIG(*)" in sql:
            return Result(scalar=self.units * 2)
        if "GROUP BY ur.soft_bin" in sql:
            assert "ur.soft_bin IS NOT NULL" in sql
            return Result(rows=[])
        raise AssertionError(sql)


class DatasetAccessConnection:
    def __init__(
        self,
        *,
        current_version_no: int = 3,
        owner_user_id: int = 7,
        has_current_version: bool = True,
    ) -> None:
        self.statements: list[str] = []
        self.executed: list[tuple[str, dict[str, object]]] = []
        self.current_version_no = current_version_no
        self.owner_user_id = owner_user_id
        self.has_current_version = has_current_version

    def execute(self, statement, parameters=None):
        sql = str(statement)
        parameters = parameters or {}
        self.statements.append(sql)
        self.executed.append((sql, parameters))
        if "SELECT TOP (1) d.dataset_id" in sql:
            if bool(parameters.get("is_admin")) or (
                parameters.get("user_id") == self.owner_user_id
            ):
                return Result(scalar=1)
            if "iam.data_scope_grant" not in sql or not self.has_current_version:
                return Result(scalar=None)
            requested_version = parameters.get("access_version_no")
            return Result(
                scalar=(
                    1
                    if requested_version is None
                    or requested_version == self.current_version_no
                    else None
                )
            )
        if "FROM dataset.dataset d" in sql and "ORDER BY d.dataset_id DESC" in sql:
            return Result(
                rows=[
                    {
                        "dataset_id": 17,
                        "dataset_code": "CURRENT-17",
                        "dataset_name": "Current dataset",
                        "dataset_type": "CP_DETAIL",
                        "test_stage": "CP",
                        "supplier_id": 2,
                        "product_id": 3,
                        "owner_user_id": self.owner_user_id,
                    }
                ]
            )
        raise AssertionError(sql)


class CompareConnection:
    def __init__(
        self,
        *,
        stage: str,
        spec_ids: dict[int, int | None],
        aggregates: dict[int, dict[str, int]],
        parameter_rows: dict[int, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.stage = stage
        self.spec_ids = spec_ids
        self.aggregates = aggregates
        self.parameter_rows = parameter_rows or {}
        self.executed: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        parameters = parameters or {}
        self.executed.append((sql, parameters))
        dataset_id = int(parameters.get("dataset_id", 0))
        if "FROM dataset.dataset_version dv" in sql and "d.supplier_id" in sql:
            return Result(
                rows=[
                    {
                        "dataset_version_id": dataset_id * 10,
                        "dataset_id": dataset_id,
                        "version_no": int(parameters["version_no"]),
                        "input_batch_id": dataset_id * 100,
                        "canonical_model_version": "1.0",
                        "status": "PUBLISHED",
                        "is_current": True,
                        "supplier_id": 2,
                        "product_id": 3,
                        "test_stage": self.stage,
                        "product_name": f"PRODUCT-{dataset_id}",
                        "spec_set_id": self.spec_ids[dataset_id],
                    }
                ]
            )
        if "COUNT_BIG(*) AS unit_count" in sql:
            for result in ("PASS", "FAIL", "UNKNOWN", "ABORT"):
                assert f"ur.overall_result='{result}'" in sql
            assert "CONVERT(bigint,1)" in sql
            return Result(rows=[self.aggregates[dataset_id]])
        if "COUNT_BIG(*) AS row_count" in sql:
            assert "analysis_parameters" in parameters
            return Result(rows=self.parameter_rows.get(dataset_id, []))
        raise AssertionError(sql)


class DetailConnection:
    def __init__(self, *, is_current: bool = True) -> None:
        self.executed: list[tuple[str, dict[str, object]]] = []
        self.is_current = is_current

    def execute(self, statement, parameters=None):
        sql = str(statement)
        parameters = parameters or {}
        self.executed.append((sql, parameters))
        if "FROM dataset.dataset_version dv" in sql and "d.supplier_id" in sql:
            return Result(
                rows=[
                    {
                        "dataset_version_id": 10,
                        "dataset_id": 1,
                        "version_no": 1,
                        "input_batch_id": 100,
                        "canonical_model_version": "1.0",
                        "status": "PUBLISHED",
                        "is_current": self.is_current,
                        "supplier_id": 2,
                        "product_id": 3,
                        "test_stage": "CP",
                        "product_name": "PRODUCT-1",
                        "spec_set_id": 7,
                    }
                ]
            )
        if "SELECT DISTINCT tr.lot_id" in sql:
            assert "tr.lot_id IS NOT NULL" in sql
            return Result(rows=[("LOT-1",)])
        if "SELECT DISTINCT COALESCE(ur.wafer_id" in sql:
            return Result(rows=[("01",)])
        if "SELECT DISTINCT COALESCE(ur.soft_bin" in sql:
            return Result(rows=[("1",), ("UNKNOWN",)])
        if "SELECT DISTINCT tid.raw_item_name" in sql:
            return Result(rows=[("P1",), ("P2",)])
        if sql.startswith("SELECT COUNT_BIG(*)"):
            return Result(scalar=3)
        if "SELECT ur.unit_id,ur.logical_unit_key" in sql:
            assert (
                "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY" in sql
            )
            if int(parameters["offset"]) >= 3:
                return Result(rows=[])
            return Result(
                rows=[
                    {
                        "unit_id": 101,
                        "logical_unit_key": "CP:SOURCE-GROUP:01:1:2:1",
                        "lot_id": None,
                        "wafer_id": "01",
                        "x_coord": 1,
                        "y_coord": 2,
                        "soft_bin": "1",
                        "hard_bin": None,
                        "overall_result": "UNKNOWN",
                        "source_row_no": 2,
                    }
                ]
            )
        if "FROM test.measurement m" in sql:
            assert parameters["unit_ids"] == (101,)
            assert parameters["detail_parameters"] == ("P1",)
            return Result(
                rows=[
                    {
                        "unit_id": 101,
                        "raw_item_name": "P1",
                        "value_numeric": None,
                        "value_text": None,
                        "measurement_status": "MISSING",
                        "unit_code": "V",
                        "program_lsl": 0.0,
                        "program_usl": 5.0,
                    }
                ]
            )
        raise AssertionError(sql)


def test_sql_dq_gate_passes_only_ready_attributable_clean_data() -> None:
    result = SqlDatasetService(Engine(GateConnection())).evaluate_gate(  # type: ignore[arg-type]
        1, 1, _SYSTEM_ADMIN
    )
    assert result.status == "PASS"
    assert result.run_count == 1
    assert result.unit_count == 10
    assert result.measurement_count == 110


def test_sql_dq_gate_reports_each_blocking_dimension() -> None:
    result = SqlDatasetService(
        Engine(GateConnection(run_status="FAILED", lineage=False, blocking=2))  # type: ignore[arg-type]
    ).evaluate_gate(1, 1, _SYSTEM_ADMIN)
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


def test_ft_charts_resolve_spec_for_selected_lot_only() -> None:
    connection = FtMultiLotSpecConnection()
    service = SqlDatasetService(Engine(connection))  # type: ignore[arg-type]
    result = service._get_ft_chart_data(
        connection,  # type: ignore[arg-type]
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
            "lot_id": "LOT-2",
            "wafer_id": None,
            "source_id": None,
            "parameter": None,
        },
        " FROM dataset.dataset_version dv JOIN dataset.dataset_version_run dvr "
        "ON dvr.dataset_version_id=dv.dataset_version_id JOIN test.test_run tr "
        "ON tr.processing_run_id=dvr.processing_run_id ",
    )

    assert result.parameter_options[0].lsl == 1.17
    assert result.parameter_options[0].test_condition == "LOT-2 condition"
    assert result.lot_options == ("LOT-1", "LOT-2")
    assert result.source_options == ("SOURCE-RUN-B",)


def test_ft_charts_reject_source_from_another_selected_lot() -> None:
    connection = FtMultiLotSpecConnection()
    service = SqlDatasetService(Engine(connection))  # type: ignore[arg-type]

    with pytest.raises(DomainError) as exc_info:
        service._get_ft_chart_data(
            connection,  # type: ignore[arg-type]
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
                "lot_id": "LOT-2",
                "wafer_id": None,
                "source_id": "SOURCE-RUN-A",
                "parameter": "HVBCES",
            },
            " FROM dataset.dataset_version dv JOIN dataset.dataset_version_run dvr "
            "ON dvr.dataset_version_id=dv.dataset_version_id JOIN test.test_run tr "
            "ON tr.processing_run_id=dvr.processing_run_id ",
        )

    assert exc_info.value.code == "FT_SOURCE_NOT_FOUND"


def test_cp_charts_exclude_unknown_and_abort_from_yield_denominator() -> None:
    connection = CpChartConnection(
        unit_count=12,
        pass_count=9,
        fail_count=1,
        unknown_count=1,
        abort_count=1,
    )
    result = SqlDatasetService(Engine(connection)).get_chart_data(1, 1)  # type: ignore[arg-type]

    point = result.wafer_yield[0]
    assert point.unit_count == 12
    assert point.pass_count == 9
    assert point.fail_count == 1
    assert point.unknown_count == 1
    assert point.abort_count == 1
    assert point.known_yield_denominator == 10
    assert point.yield_rate == pytest.approx(0.9)


def test_cp_charts_return_null_yield_without_known_results() -> None:
    connection = CpChartConnection(
        unit_count=12,
        pass_count=0,
        fail_count=0,
        unknown_count=10,
        abort_count=2,
    )
    result = SqlDatasetService(Engine(connection)).get_chart_data(1, 1)  # type: ignore[arg-type]

    point = result.wafer_yield[0]
    assert point.known_yield_denominator == 0
    assert point.yield_rate is None


@pytest.mark.parametrize(
    ("status", "is_current"),
    (("PUBLISHED", False), ("VALIDATING", True)),
)
def test_chart_rejects_versions_outside_current_published_contract(
    status: str, is_current: bool
) -> None:
    connection = CpChartConnection(
        unit_count=1,
        pass_count=1,
        fail_count=0,
        unknown_count=0,
        abort_count=0,
        status=status,
        is_current=is_current,
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).get_chart_data(1, 1)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_VERSION_NOT_CURRENT"
    assert error.value.status_code == 409


def test_dataset_summary_keeps_unknown_pass_fail_and_yield_null() -> None:
    result = SqlDatasetService(
        Engine(SummaryConnection(units=10, passes=0, failures=0))  # type: ignore[arg-type]
    ).get_summary(1, 1, _SYSTEM_ADMIN)

    assert result.unit_count == 10
    assert result.pass_count is None
    assert result.fail_count is None
    assert result.yield_rate is None
    assert result.bin_counts == {}


def test_dataset_summary_excludes_unknown_units_from_yield_denominator() -> None:
    result = SqlDatasetService(
        Engine(SummaryConnection(units=12, passes=9, failures=1))  # type: ignore[arg-type]
    ).get_summary(1, 1, _SYSTEM_ADMIN)

    assert result.pass_count == 9
    assert result.fail_count == 1
    assert result.yield_rate == pytest.approx(0.9)


def test_manager_global_read_scope_only_allows_the_requested_current_version() -> None:
    connection = DatasetAccessConnection()
    service = SqlDatasetService(Engine(connection))  # type: ignore[arg-type]
    manager = Principal(
        8,
        "manager.viewer",
        "Manager Viewer",
        ("MANAGER_VIEWER",),
        frozenset({"DATASET_READ"}),
    )

    service.assert_dataset_access(17, manager, "READ", version_no=3)
    with pytest.raises(DomainError) as historical_error:
        service.assert_dataset_access(17, manager, "READ", version_no=2)
    with pytest.raises(DomainError) as error:
        service.assert_dataset_access(17, manager, "WRITE", version_no=3)

    assert historical_error.value.code == "DATASET_ACCESS_DENIED"
    assert error.value.code == "DATASET_ACCESS_DENIED"
    assert "scope_g.scope_key=N'TMS_CURRENT_DATA'" in connection.statements[0]
    assert "access_dv.version_no=:access_version_no" in connection.statements[0]
    assert "access_dv.status='PUBLISHED'" in connection.statements[0]
    assert "access_dv.is_current=1" in connection.statements[0]
    assert connection.executed[0][1]["access_version_no"] == 3
    assert "iam.data_scope_grant" not in connection.statements[2]


@pytest.mark.parametrize(
    "principal",
    (
        Principal(
            7,
            "dataset.owner",
            "Dataset Owner",
            ("DATA_ENGINEER",),
            frozenset({"DATASET_READ"}),
        ),
        Principal(
            1,
            "system.admin",
            "System Admin",
            ("SYSTEM_ADMIN",),
            frozenset({"DATASET_READ"}),
        ),
    ),
    ids=("owner", "system-admin"),
)
def test_owner_and_admin_keep_historical_version_read_access(
    principal: Principal,
) -> None:
    service = SqlDatasetService(Engine(DatasetAccessConnection()))  # type: ignore[arg-type]

    service.assert_dataset_access(17, principal, "READ", version_no=2)


def test_dataset_list_grant_branch_requires_a_current_published_version() -> None:
    connection = DatasetAccessConnection()
    manager = Principal(
        8,
        "manager.viewer",
        "Manager Viewer",
        ("MANAGER_VIEWER",),
        frozenset({"DATASET_READ"}),
    )

    result = SqlDatasetService(Engine(connection)).list_datasets(manager)  # type: ignore[arg-type]

    assert result[0].dataset_id == 17
    sql = connection.statements[0]
    assert "scope_g.scope_key=N'TMS_CURRENT_DATA'" in sql
    assert "access_dv.status='PUBLISHED'" in sql
    assert "access_dv.is_current=1" in sql


def test_gate_core_rechecks_scope_and_preserves_owner_history() -> None:
    manager = Principal(
        8,
        "manager.viewer",
        "Manager Viewer",
        ("MANAGER_VIEWER",),
        frozenset({"DATASET_READ"}),
    )
    owner = Principal(
        7,
        "dataset.owner",
        "Dataset Owner",
        ("DATA_ENGINEER",),
        frozenset({"DATASET_READ"}),
    )

    with pytest.raises(DomainError) as denied:
        SqlDatasetService(Engine(GateConnection())).evaluate_gate(  # type: ignore[arg-type]
            1, 1, manager
        )
    owner_result = SqlDatasetService(Engine(GateConnection())).evaluate_gate(  # type: ignore[arg-type]
        1, 1, owner
    )

    assert denied.value.code == "DATASET_VERSION_NOT_FOUND"
    assert owner_result.status == "PASS"


def test_summary_core_rechecks_scope_and_preserves_admin_history() -> None:
    manager = Principal(
        8,
        "manager.viewer",
        "Manager Viewer",
        ("MANAGER_VIEWER",),
        frozenset({"DATASET_READ"}),
    )
    admin = Principal(
        1,
        "system.admin",
        "System Admin",
        ("SYSTEM_ADMIN",),
        frozenset({"DATASET_READ"}),
    )
    historical = {
        "version_status": "SUPERSEDED",
        "is_current": False,
        "units": 10,
        "passes": 9,
        "failures": 1,
    }

    with pytest.raises(DomainError) as denied:
        SqlDatasetService(Engine(SummaryConnection(**historical))).get_summary(  # type: ignore[arg-type]
            1, 1, manager
        )
    admin_result = SqlDatasetService(
        Engine(SummaryConnection(**historical))  # type: ignore[arg-type]
    ).get_summary(1, 1, admin)

    assert denied.value.code == "DATASET_VERSION_NOT_FOUND"
    assert admin_result.version_status == "SUPERSEDED"
    assert admin_result.is_current is False


def test_cp_compare_fails_closed_when_spec_set_identity_conflicts() -> None:
    connection = CompareConnection(
        stage="CP",
        spec_ids={1: 10, 2: 11},
        aggregates={},
    )
    request = DatasetComparisonRequest(
        datasets=[
            {"dataset_id": 1, "version_no": 1},
            {"dataset_id": 2, "version_no": 1},
        ]
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).compare(request)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_SPEC_INCOMPATIBLE"
    assert all("COUNT_BIG(*) AS unit_count" not in sql for sql, _ in connection.executed)


def test_cp_compare_uses_known_yield_and_normalized_parameter_contract() -> None:
    parameter_row = {
        "raw_item_name": "P1",
        "unit_code": " V ",
        "program_lsl": 0.0,
        "program_usl": 5.0,
        "condition_json": '{ "text": "VGS=0V   IDS=1mA" }',
        "row_count": 12,
        "missing_count": 1,
        "minimum": 0.1,
        "maximum": 4.9,
        "average": 2.5,
    }
    connection = CompareConnection(
        stage="CP",
        spec_ids={1: 10, 2: 10},
        aggregates={
            1: {
                "unit_count": 12,
                "pass_count": 9,
                "fail_count": 1,
                "unknown_count": 1,
                "abort_count": 1,
            },
            2: {
                "unit_count": 5,
                "pass_count": 0,
                "fail_count": 0,
                "unknown_count": 5,
                "abort_count": 0,
            },
        },
        parameter_rows={1: [parameter_row], 2: [{**parameter_row, "row_count": 5}]},
    )
    request = DatasetComparisonRequest(
        datasets=[
            {"dataset_id": 1, "version_no": 1},
            {"dataset_id": 2, "version_no": 1},
        ],
        parameters=["P1"],
    )

    result = SqlDatasetService(Engine(connection)).compare(request)  # type: ignore[arg-type]

    assert result.spec_compatibility == "COMPATIBLE"
    assert result.items[0].known_yield_denominator == 10
    assert result.items[0].yield_rate == pytest.approx(0.9)
    assert result.items[1].known_yield_denominator == 0
    assert result.items[1].yield_rate is None
    statistic = result.items[0].parameter_statistics[0]
    assert statistic.unit == "V"
    assert statistic.test_condition == "VGS=0V IDS=1mA"
    assert statistic.measured_count == 11
    assert statistic.missing_count == 1


def test_ft_compare_aggregates_without_inventing_unknown_yield() -> None:
    parameter_row = {
        "raw_item_name": "HVBCES",
        "unit_code": "kV",
        "program_lsl": 1.29,
        "program_usl": None,
        "condition_json": '{"text":"VGE=0V"}',
        "row_count": 5,
        "missing_count": 0,
        "minimum": 1.3,
        "maximum": 1.5,
        "average": 1.4,
    }
    connection = CompareConnection(
        stage="FT",
        spec_ids={1: None, 2: None},
        aggregates={
            1: {
                "unit_count": 5,
                "pass_count": 0,
                "fail_count": 0,
                "unknown_count": 5,
                "abort_count": 0,
            },
            2: {
                "unit_count": 10,
                "pass_count": 7,
                "fail_count": 3,
                "unknown_count": 0,
                "abort_count": 0,
            },
        },
        parameter_rows={1: [parameter_row], 2: [parameter_row]},
    )
    request = DatasetComparisonRequest(
        datasets=[
            {"dataset_id": 1, "version_no": 1},
            {"dataset_id": 2, "version_no": 1},
        ],
        parameters=["HVBCES"],
    )

    result = SqlDatasetService(Engine(connection)).compare(request)  # type: ignore[arg-type]

    assert result.test_stage == "FT"
    assert result.spec_compatibility == "COMPATIBLE"
    assert result.items[0].yield_rate is None
    assert result.items[1].yield_rate == pytest.approx(0.7)


def test_ft_compare_rejects_selected_parameter_unit_conflict() -> None:
    base_row = {
        "raw_item_name": "HVBCES",
        "program_lsl": 1.29,
        "program_usl": None,
        "condition_json": '{"text":"VGE=0V"}',
        "row_count": 5,
        "missing_count": 0,
        "minimum": 1.3,
        "maximum": 1.5,
        "average": 1.4,
    }
    connection = CompareConnection(
        stage="FT",
        spec_ids={1: None, 2: None},
        aggregates={
            dataset_id: {
                "unit_count": 5,
                "pass_count": 0,
                "fail_count": 0,
                "unknown_count": 5,
                "abort_count": 0,
            }
            for dataset_id in (1, 2)
        },
        parameter_rows={
            1: [{**base_row, "unit_code": "kV"}],
            2: [{**base_row, "unit_code": "V"}],
        },
    )
    request = DatasetComparisonRequest(
        datasets=[
            {"dataset_id": 1, "version_no": 1},
            {"dataset_id": 2, "version_no": 1},
        ],
        parameters=["HVBCES"],
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).compare(request)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_PARAMETER_INCOMPATIBLE"


def test_compare_rejects_non_reconciling_status_aggregates() -> None:
    connection = CompareConnection(
        stage="FT",
        spec_ids={1: None},
        aggregates={
            1: {
                "unit_count": 5,
                "pass_count": 1,
                "fail_count": 1,
                "unknown_count": 1,
                "abort_count": 1,
            }
        },
    )
    request = DatasetComparisonRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}]
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).compare(request)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_AGGREGATE_CONTRACT_INVALID"


def test_detail_page_uses_sql2014_pagination_and_preserves_nulls() -> None:
    connection = DetailConnection()

    result = SqlDatasetService(Engine(connection)).get_detail_page(  # type: ignore[arg-type]
        1,
        1,
        page=2,
        page_size=2,
        lot_ids=(" LOT-1 ",),
        wafer_ids=("01",),
        bin_codes=("1",),
        parameters=("P1",),
    )

    assert result.total == 3
    assert result.page == 2
    assert result.page_size == 2
    assert result.lot_options == ("LOT-1",)
    assert result.items[0].lot_id is None
    assert result.items[0].measurements[0].value_numeric is None
    unit_query = next(
        (sql, params)
        for sql, params in connection.executed
        if "SELECT ur.unit_id,ur.logical_unit_key" in sql
    )
    assert unit_query[1]["offset"] == 2
    assert "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY" in unit_query[0]
    assert "STRING_AGG" not in unit_query[0]
    assert "OPENJSON" not in unit_query[0]


def test_detail_page_allows_empty_page_beyond_total() -> None:
    result = SqlDatasetService(Engine(DetailConnection())).get_detail_page(  # type: ignore[arg-type]
        1, 1, page=3, page_size=2
    )

    assert result.total == 3
    assert result.items == ()


def test_detail_page_rejects_a_non_current_published_version() -> None:
    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(DetailConnection(is_current=False))).get_detail_page(  # type: ignore[arg-type]
            1, 1, page=1, page_size=50
        )

    assert error.value.code == "ANALYSIS_VERSION_NOT_CURRENT"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"page": 0, "page_size": 50},
        {"page": 1, "page_size": 201},
        {"page": 10_737_420, "page_size": 200},
        {"page": 1, "page_size": 50, "lot_ids": tuple(f"L{n}" for n in range(51))},
        {"page": 1, "page_size": 50, "parameters": ("P1", " P1 ")},
    ],
    ids=("page-zero", "page-size", "sql-offset", "lot-limit", "duplicate-filter"),
)
def test_detail_page_rejects_invalid_pagination_and_filter_boundaries(kwargs) -> None:
    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(DetailConnection())).get_detail_page(  # type: ignore[arg-type]
            1, 1, **kwargs
        )

    assert error.value.status_code == 422
