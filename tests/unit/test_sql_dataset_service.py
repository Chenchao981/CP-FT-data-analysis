from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.datasets import (
    DatasetComparisonRequest,
    DatasetParameterAnalysisRequest,
    DqGateResult,
    PublishDatasetVersionRequest,
)
from app.infrastructure.sql_dataset_service import (
    SqlDatasetService,
    _parameter_analysis_filter_hash,
)

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
    def __init__(self, *, rows=None, scalar=None, rowcount: int = 0) -> None:
        self.rows = rows or []
        self.scalar = scalar
        self.rowcount = rowcount

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
        business_domain="PRODUCTION",
    ) -> None:
        self.run_status = run_status
        self.lineage = lineage
        self.blocking = blocking
        self.version_status = version_status
        self.is_current = is_current
        self.owner_user_id = owner_user_id
        self.business_domain = business_domain

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "FROM dataset.dataset_version dv" in sql and "d.supplier_id" in sql:
            parameters = parameters or {}
            if "access_b.business_domain='PRODUCTION'" in sql and not (
                bool(parameters.get("is_admin"))
                or parameters.get("user_id") == self.owner_user_id
                or (
                    self.business_domain == "PRODUCTION"
                    and self.version_status == "PUBLISHED"
                    and self.is_current
                )
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


class PublishConnection:
    def __init__(
        self,
        *,
        previous_version_id: int | None,
        previous_run_has_other_current: bool = False,
    ) -> None:
        self.previous_version_id = previous_version_id
        self.previous_run_has_other_current = previous_run_has_other_current
        self.statements: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        parameters = parameters or {}
        self.statements.append((sql, parameters))
        if sql.startswith("SELECT COUNT(*) FROM dataset.dataset_version_run"):
            return Result(scalar=1)
        if sql.startswith("SELECT status FROM iam.app_user"):
            return Result(scalar="ACTIVE")
        if sql.startswith("SELECT dataset_version_id FROM dataset.dataset_version"):
            return Result(scalar=self.previous_version_id)
        if sql.startswith("SELECT pr.processing_run_id,pr.status,pr.is_current"):
            return Result(
                rows=[
                    {
                        "processing_run_id": 70,
                        "status": "PUBLISHED",
                        "is_current": True,
                        "has_other_current": self.previous_run_has_other_current,
                    }
                ]
            )
        if sql.startswith("UPDATE dataset.dataset_version SET status='SUPERSEDED'"):
            return Result(rowcount=1)
        if sql.startswith("UPDATE pr SET pr.status='SUPERSEDED'"):
            return Result(rowcount=0 if self.previous_run_has_other_current else 1)
        if sql.startswith("UPDATE pr SET pr.status='PUBLISHED'"):
            return Result(rowcount=1)
        if sql.startswith("UPDATE dataset.dataset_version SET status='PUBLISHED'"):
            return Result(
                rows=[
                    {
                        "dataset_version_id": 5,
                        "dataset_id": 1,
                        "version_no": 2,
                        "input_batch_id": 8,
                        "canonical_model_version": "1.0",
                        "status": "PUBLISHED",
                        "is_current": True,
                    }
                ],
                rowcount=1,
            )
        raise AssertionError(sql)


class PublishService(SqlDatasetService):
    def _version_context(self, connection, dataset_id, version_no, **kwargs):
        return {
            "dataset_version_id": 5,
            "dataset_id": dataset_id,
            "version_no": version_no,
            "input_batch_id": 8,
            "canonical_model_version": "1.0",
            "status": "DRAFT",
            "is_current": False,
        }

    def _evaluate(self, connection, dataset_id, version_no, **kwargs):
        return DqGateResult(
            dataset_id=dataset_id,
            version_no=version_no,
            status="PASS",
            run_count=1,
            unit_count=10,
            measurement_count=100,
            reasons=(),
        )


def test_manual_publish_allows_same_source_to_remain_current_in_another_dataset() -> (
    None
):
    connection = PublishConnection(previous_version_id=None)
    service = PublishService(Engine(connection))  # type: ignore[arg-type]

    result = service.publish(
        1,
        2,
        PublishDatasetVersionRequest(published_by=7),
    )

    assert result.is_current is True
    statements = [sql for sql, _ in connection.statements]
    assert not any("status='SUPERSEDED'" in sql for sql in statements)
    assert not any("source_file_id=prior.source_file_id" in sql for sql in statements)


def test_manual_publish_reprocess_supersedes_only_same_dataset_previous_runs() -> None:
    connection = PublishConnection(previous_version_id=4)
    service = PublishService(Engine(connection))  # type: ignore[arg-type]

    service.publish(
        1,
        2,
        PublishDatasetVersionRequest(published_by=7),
    )

    previous_select, select_parameters = next(
        (sql, parameters)
        for sql, parameters in connection.statements
        if sql.startswith("SELECT pr.processing_run_id,pr.status,pr.is_current")
    )
    assert "dvr.dataset_version_id=:previous_id" in previous_select
    assert "source_file_id" not in previous_select
    assert select_parameters == {"previous_id": 4}
    demotion_sql, demotion_parameters = next(
        (sql, parameters)
        for sql, parameters in connection.statements
        if sql.startswith("UPDATE pr SET pr.status='SUPERSEDED'")
    )
    assert "dvr.dataset_version_id=:previous_id" in demotion_sql
    assert "source_file_id" not in demotion_sql
    assert demotion_parameters == {"previous_id": 4}


def test_manual_publish_keeps_run_current_if_another_dataset_still_uses_it() -> None:
    connection = PublishConnection(
        previous_version_id=4,
        previous_run_has_other_current=True,
    )
    service = PublishService(Engine(connection))  # type: ignore[arg-type]

    result = service.publish(
        1,
        2,
        PublishDatasetVersionRequest(published_by=7),
    )

    assert result.is_current is True
    demotion_sql = next(
        sql
        for sql, _ in connection.statements
        if sql.startswith("UPDATE pr SET pr.status='SUPERSEDED'")
    )
    assert "NOT EXISTS" in demotion_sql
    assert "other_dv.status='PUBLISHED'" in demotion_sql


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
            if "(:lot_id IS NULL OR tr.lot_id=:lot_id)" in sql and parameters.get(
                "lot_id"
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
        business_domain: str = "PRODUCTION",
    ) -> None:
        self.units = units
        self.passes = passes
        self.failures = failures
        self.version_status = version_status
        self.is_current = is_current
        self.owner_user_id = owner_user_id
        self.business_domain = business_domain

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "COUNT(DISTINCT dvr.processing_run_id)" in sql:
            parameters = parameters or {}
            if "access_b.business_domain='PRODUCTION'" in sql and not (
                bool(parameters.get("is_admin"))
                or parameters.get("user_id") == self.owner_user_id
                or (
                    self.business_domain == "PRODUCTION"
                    and self.version_status == "PUBLISHED"
                    and self.is_current
                )
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
        business_domain: str = "PRODUCTION",
    ) -> None:
        self.statements: list[str] = []
        self.executed: list[tuple[str, dict[str, object]]] = []
        self.current_version_no = current_version_no
        self.owner_user_id = owner_user_id
        self.has_current_version = has_current_version
        self.business_domain = business_domain

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
            if (
                "access_b.business_domain='PRODUCTION'" not in sql
                or not self.has_current_version
                or self.business_domain != "PRODUCTION"
            ):
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
            assert "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY" in sql
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


def _analysis_aggregate_row(
    *,
    row_count: int = 8,
    numeric_count: int = 8,
    measured_count: int | None = None,
    over_range: int = 0,
    under_range: int = 0,
    missing: int = 0,
    minimum: float = 1.0,
    maximum: float = 100.0,
    average: float = 16.0,
    sample_stddev: float = 34.0,
) -> dict[str, Any]:
    measured = numeric_count if measured_count is None else measured_count
    return {
        "raw_item_name": "P1",
        "row_count": row_count,
        "numeric_count": numeric_count,
        "status_measured": measured,
        "status_over_range": over_range,
        "status_under_range": under_range,
        "status_not_tested": 0,
        "status_missing": missing,
        "status_invalid": 0,
        "status_not_applicable": 0,
        "minimum": minimum,
        "maximum": maximum,
        "average": average,
        "sample_stddev": sample_stddev,
    }


class ParameterAnalysisConnection:
    def __init__(
        self,
        *,
        stage_by_dataset: dict[int, str] | None = None,
        status_by_dataset: dict[int, str] | None = None,
        current_by_dataset: dict[int, bool] | None = None,
        context_spec_by_dataset: dict[int, int | None] | None = None,
        identity_rows_by_dataset: dict[int, list[dict[str, Any]]] | None = None,
        spec_rows_by_dataset: dict[int, list[dict[str, Any]]] | None = None,
        matched_unit_count: int = 8,
        candidate_measurement_count: int = 8,
        aggregate_rows: list[dict[str, Any]] | None = None,
        box_rows: list[dict[str, Any]] | None = None,
        histogram_rows: list[dict[str, Any]] | None = None,
        subgroup_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.stage_by_dataset = stage_by_dataset or {}
        self.status_by_dataset = status_by_dataset or {}
        self.current_by_dataset = current_by_dataset or {}
        self.context_spec_by_dataset = context_spec_by_dataset or {}
        self.identity_rows_by_dataset = identity_rows_by_dataset or {}
        self.spec_rows_by_dataset = spec_rows_by_dataset or {}
        self.matched_unit_count = matched_unit_count
        self.candidate_measurement_count = candidate_measurement_count
        self.aggregate_rows = (
            aggregate_rows
            if aggregate_rows is not None
            else [_analysis_aggregate_row()]
        )
        self.box_rows = (
            box_rows
            if box_rows is not None
            else [
                {
                    "raw_item_name": "P1",
                    "minimum": 1.0,
                    "q1": 2.75,
                    "median": 4.5,
                    "q3": 6.25,
                    "maximum": 100.0,
                    "lower_whisker": 1.0,
                    "upper_whisker": 7.0,
                    "outlier_count": 1,
                }
            ]
        )
        self.histogram_rows = (
            histogram_rows
            if histogram_rows is not None
            else [
                {
                    "raw_item_name": "P1",
                    "range_min": 1.0,
                    "range_max": 100.0,
                    "bin_index": 0,
                    "bin_value_count": 7,
                },
                {
                    "raw_item_name": "P1",
                    "range_min": 1.0,
                    "range_max": 100.0,
                    "bin_index": 19,
                    "bin_value_count": 1,
                },
            ]
        )
        self.subgroup_rows = subgroup_rows or []
        self.executed: list[tuple[str, dict[str, object]]] = []

    @staticmethod
    def _identity_rows() -> list[dict[str, Any]]:
        return [
            {
                "run_id": 101,
                "run_program_version_id": 201,
                "test_item_id": 301,
                "program_version_id": 201,
                "step_code": "STEP_P1",
                "sequence_no": 1,
                "raw_item_name": "P1",
                "canonical_parameter_code": "P1",
                "unit_code": "V",
                "program_lsl": 0.0,
                "program_usl": 10.0,
                "condition_json": '{"text":"VGS=0V"}',
            }
        ]

    @staticmethod
    def _spec_rows(spec_set_id: int = 7) -> list[dict[str, Any]]:
        return [
            {
                "run_id": 101,
                "run_program_version_id": 201,
                "item_program_version_id": 201,
                "test_item_id": 301,
                "lot_id": "LOT-1",
                "wafer_id": "01",
                "raw_item_name": "P1",
                "spec_set_id": spec_set_id,
                "spec_item_id": spec_set_id * 10 + 1,
                "unit_code": "V",
                "lsl": 0.0,
                "usl": 10.0,
                "condition_json": '{"text":"VGS=0V"}',
            }
        ]

    def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        parameters = parameters or {}
        self.executed.append((sql, parameters))
        dataset_id = int(parameters.get("dataset_id", 1))
        if "FROM dataset.dataset_version dv" in sql and "d.supplier_id" in sql:
            return Result(
                rows=[
                    {
                        "dataset_version_id": dataset_id * 10,
                        "dataset_id": dataset_id,
                        "version_no": int(parameters["version_no"]),
                        "input_batch_id": dataset_id * 100,
                        "canonical_model_version": "1.0",
                        "status": self.status_by_dataset.get(dataset_id, "PUBLISHED"),
                        "is_current": self.current_by_dataset.get(dataset_id, True),
                        "supplier_id": 2,
                        "product_id": 3,
                        "test_stage": self.stage_by_dataset.get(dataset_id, "CP"),
                        "product_name": f"PRODUCT-{dataset_id}",
                        "spec_set_id": self.context_spec_by_dataset.get(dataset_id, 7),
                        "unit_count": 10,
                    }
                ]
            )
        if sql.startswith("SELECT DISTINCT tr.run_id,tr.tester_id,tr.metadata_json"):
            return Result(
                rows=[
                    {
                        "run_id": dataset_id * 100 + 1,
                        "tester_id": "TESTER-A",
                        "metadata_json": '{"source_id":"SOURCE-A"}',
                    }
                ]
            )
        if (
            sql.startswith(
                "SELECT DISTINCT tr.run_id,tr.program_version_id AS run_program_version_id"
            )
            and "tid.condition_json" in sql
        ):
            return Result(
                rows=self.identity_rows_by_dataset.get(
                    dataset_id, self._identity_rows()
                )
            )
        if sql.startswith(";WITH filtered_units AS"):
            assert parameters["analysis_test_item_ids"] == (301,)
            return Result(
                rows=[
                    {
                        "matched_unit_count": self.matched_unit_count,
                        "candidate_measurement_count": self.candidate_measurement_count,
                    }
                ]
            )
        if "AS status_measured" in sql:
            assert parameters["analysis_test_item_ids"] == (301,)
            return Result(rows=self.aggregate_rows)
        if "quartiles AS" in sql:
            assert parameters["analysis_test_item_ids"] == (301,)
            return Result(rows=self.box_rows)
        if "bucketed AS" in sql:
            assert parameters["analysis_test_item_ids"] == (301,)
            return Result(rows=self.histogram_rows)
        if "si.spec_item_id" in sql and "AS item_program_version_id" in sql:
            assert parameters["analysis_test_item_ids"] == (301,)
            return Result(
                rows=self.spec_rows_by_dataset.get(
                    dataset_id,
                    self._spec_rows(
                        int(self.context_spec_by_dataset.get(dataset_id, 7) or 7)
                    ),
                )
            )
        if " AS subgroup_key,MIN(" in sql:
            assert parameters["analysis_test_item_ids"] == (301,)
            return Result(rows=self.subgroup_rows)
        raise AssertionError(sql)


def _approved_parameter_analysis_service(connection) -> SqlDatasetService:
    return SqlDatasetService(
        Engine(connection),  # type: ignore[arg-type]
        approved_parameter_analysis_rule_codes=frozenset(
            {
                "TUKEY_1_5_IQR_PERCENTILE_CONT_LINEAR_V1",
                "EQUAL_WIDTH_FIXED_BINS_LAST_CLOSED_V1",
                "CPK_POOLED_WITHIN_RUN_V1",
                "CPK_POOLED_WITHIN_LOT_WAFER_V1",
            }
        ),
    )


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


def test_non_owner_production_read_only_allows_the_requested_current_version() -> None:
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
    assert "access_b.business_domain='PRODUCTION'" in connection.statements[0]
    assert "access_dv.version_no=:access_version_no" in connection.statements[0]
    assert "access_dv.status='PUBLISHED'" in connection.statements[0]
    assert "access_dv.is_current=1" in connection.statements[0]
    assert connection.executed[0][1]["access_version_no"] == 3
    assert "access_b.business_domain='PRODUCTION'" not in connection.statements[2]


def test_non_owner_engineering_current_is_denied() -> None:
    service = SqlDatasetService(  # type: ignore[arg-type]
        Engine(DatasetAccessConnection(business_domain="ENGINEERING"))
    )
    viewer = Principal(
        8,
        "production.viewer",
        "Production Viewer",
        ("MANAGER_VIEWER",),
        frozenset({"DATASET_READ"}),
    )

    with pytest.raises(DomainError) as error:
        service.assert_dataset_access(17, viewer, "READ", version_no=3)

    assert error.value.code == "DATASET_ACCESS_DENIED"


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


def test_dataset_list_production_branch_requires_a_current_published_version() -> None:
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
    assert "access_b.business_domain='PRODUCTION'" in sql
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
    assert all(
        "COUNT_BIG(*) AS unit_count" not in sql for sql, _ in connection.executed
    )


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
    request = DatasetComparisonRequest(datasets=[{"dataset_id": 1, "version_no": 1}])

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


def test_parameter_analysis_returns_tukey_box_histogram_and_filter_summary() -> None:
    connection = ParameterAnalysisConnection()
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        filters={
            "lot_ids": ["LOT-1"],
            "wafer_ids": ["01"],
            "bin_codes": ["1"],
            "overall_results": ["PASS"],
            "source_ids": ["SOURCE-A"],
        },
        parameters=["P1"],
        analyses=["DESCRIPTIVE", "BOX_PLOT", "HISTOGRAM"],
        histogram={"bin_count": 20},
    )

    result = _approved_parameter_analysis_service(connection).analyze_parameters(
        request
    )

    assert result.contract_version == "PARAMETER_ANALYSIS_V1"
    assert result.group_by == "DATASET"
    assert result.compatibility == "SINGLE_DATASET"
    assert result.dataset_context.current_published_verified is True
    assert result.dataset_context.test_stage == "CP"
    assert result.dataset_context.resolved_datasets[0].dataset_id == 1
    assert len(result.filter_summary.filter_hash) == 64
    assert result.filter_summary.normalized_filters.source_ids == ("SOURCE-A",)
    assert result.rule_context.spec_versions == ("SPEC_SET:7",)
    assert result.rule_context.evaluation_rule_versions == (
        "TUKEY_1_5_IQR_PERCENTILE_CONT_LINEAR_V1",
        "EQUAL_WIDTH_FIXED_BINS_LAST_CLOSED_V1",
    )
    assert result.counts.input_units == 10
    assert result.counts.included_units == 8
    assert result.counts.excluded_units == 2
    assert result.sampling_summary.sampled is False
    assert result.warnings == ()
    assert result.computed_at.endswith("+00:00")
    item = result.items[0]
    assert item.filter_summary.source_ids == ("SOURCE-A",)
    assert item.filter_summary.overall_results == ("PASS",)
    assert item.filter_summary.matched_unit_count == 8
    assert item.filter_summary.candidate_measurement_count == 8
    parameter = item.parameters[0]
    assert {row.status: row.count for row in parameter.status_counts}["MEASURED"] == 8
    assert parameter.descriptive is not None
    assert parameter.descriptive.sample_stddev == pytest.approx(34.0)
    assert parameter.box_plot is not None
    assert parameter.box_plot.q1 == pytest.approx(2.75)
    assert parameter.box_plot.median == pytest.approx(4.5)
    assert parameter.box_plot.q3 == pytest.approx(6.25)
    assert parameter.box_plot.lower_whisker == pytest.approx(1.0)
    assert parameter.box_plot.upper_whisker == pytest.approx(7.0)
    assert parameter.box_plot.outlier_count == 1
    assert parameter.box_plot.method == "TUKEY_1_5_IQR_PERCENTILE_CONT_LINEAR_V1"
    assert parameter.histogram is not None
    assert parameter.histogram.bin_count == 20
    assert parameter.histogram.method == "EQUAL_WIDTH_FIXED_BINS_LAST_CLOSED_V1"
    assert sum(bucket.count for bucket in parameter.histogram.bins) == 8
    assert parameter.histogram.bins[-1].upper_inclusive is True
    aggregate_sql, aggregate_params = next(
        (sql, params)
        for sql, params in connection.executed
        if "AS status_measured" in sql
    )
    assert "tr.lot_id IN" in aggregate_sql
    assert "COALESCE(ur.wafer_id,tr.wafer_id) IN" in aggregate_sql
    assert "ur.overall_result IN" in aggregate_sql
    assert "tr.run_id IN" in aggregate_sql
    assert aggregate_params["source_run_ids"] == (101,)


def test_parameter_analysis_filter_hash_is_order_independent_within_dimensions() -> (
    None
):
    first = _parameter_analysis_filter_hash(
        lot_ids=("LOT-2", "LOT-1"),
        wafer_ids=("02", "01"),
        bin_codes=("2", "1"),
        overall_results=("UNKNOWN", "PASS"),
        source_ids=("SOURCE-B", "SOURCE-A"),
    )
    second = _parameter_analysis_filter_hash(
        lot_ids=("LOT-1", "LOT-2"),
        wafer_ids=("01", "02"),
        bin_codes=("1", "2"),
        overall_results=("PASS", "UNKNOWN"),
        source_ids=("SOURCE-A", "SOURCE-B"),
    )

    assert first == second
    assert len(first) == 64


def test_parameter_analysis_box_and_histogram_are_stable_for_constant_values() -> None:
    connection = ParameterAnalysisConnection(
        matched_unit_count=4,
        candidate_measurement_count=4,
        aggregate_rows=[
            _analysis_aggregate_row(
                row_count=4,
                numeric_count=4,
                minimum=5.0,
                maximum=5.0,
                average=5.0,
                sample_stddev=0.0,
            )
        ],
        box_rows=[
            {
                "raw_item_name": "P1",
                "minimum": 5.0,
                "q1": 5.0,
                "median": 5.0,
                "q3": 5.0,
                "maximum": 5.0,
                "lower_whisker": 5.0,
                "upper_whisker": 5.0,
                "outlier_count": 0,
            }
        ],
        histogram_rows=[
            {
                "raw_item_name": "P1",
                "range_min": 5.0,
                "range_max": 5.0,
                "bin_index": 0,
                "bin_value_count": 4,
            }
        ],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["BOX_PLOT", "HISTOGRAM"],
    )

    result = _approved_parameter_analysis_service(connection).analyze_parameters(
        request
    )

    parameter = result.items[0].parameters[0]
    assert parameter.box_plot is not None
    assert parameter.box_plot.lower_whisker == parameter.box_plot.upper_whisker == 5.0
    assert parameter.histogram is not None
    assert parameter.histogram.bin_count == 1
    assert parameter.histogram.bins[0].lower_bound == 5.0
    assert parameter.histogram.bins[0].upper_bound == 5.0
    assert parameter.histogram.bins[0].count == 4


def test_parameter_analysis_box_preserves_zero_lower_whisker_for_negative_outlier() -> (
    None
):
    connection = ParameterAnalysisConnection(
        aggregate_rows=[
            _analysis_aggregate_row(
                row_count=8,
                numeric_count=8,
                minimum=-100.0,
                maximum=6.0,
                average=-9.875,
                sample_stddev=36.5,
            )
        ],
        box_rows=[
            {
                "raw_item_name": "P1",
                "minimum": -100.0,
                "q1": 0.75,
                "median": 2.5,
                "q3": 4.25,
                "maximum": 6.0,
                "lower_whisker": 0.0,
                "upper_whisker": 6.0,
                "outlier_count": 1,
            }
        ],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["BOX_PLOT"],
    )

    result = _approved_parameter_analysis_service(connection).analyze_parameters(
        request
    )

    box = result.items[0].parameters[0].box_plot
    assert box is not None
    assert box.minimum == -100.0
    assert box.lower_whisker == 0.0
    assert box.upper_whisker == 6.0
    assert box.outlier_count == 1


def test_parameter_analysis_requires_explicit_capability_rule() -> None:
    connection = ParameterAnalysisConnection(
        matched_unit_count=40,
        candidate_measurement_count=40,
        aggregate_rows=[
            _analysis_aggregate_row(
                row_count=40,
                numeric_count=40,
                minimum=2.0,
                maximum=8.0,
                average=5.0,
                sample_stddev=1.0,
            )
        ],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["CAPABILITY"],
    )

    result = SqlDatasetService(Engine(connection)).analyze_parameters(request)  # type: ignore[arg-type]

    capability = result.items[0].parameters[0].capability
    assert capability is not None
    assert capability.status == "NOT_ELIGIBLE"
    assert capability.ppk_status == "NOT_REQUESTED"
    assert capability.cpk_status == "NOT_REQUESTED"
    assert capability.reason_codes == ("CAPABILITY_RULE_REQUIRED",)
    assert capability.ppk is None
    assert capability.cpk is None
    assert capability.overall_sigma is None
    assert capability.within_sigma is None
    assert not any(
        sql.startswith("SELECT DISTINCT tid.raw_item_name,ss.spec_set_id")
        or " AS subgroup_key,MIN(" in sql
        for sql, _ in connection.executed
    )


def test_parameter_analysis_explicit_run_rule_calculates_ppk_and_single_subgroup_cpk() -> (
    None
):
    connection = ParameterAnalysisConnection(
        matched_unit_count=40,
        candidate_measurement_count=40,
        aggregate_rows=[
            _analysis_aggregate_row(
                row_count=40,
                numeric_count=40,
                minimum=2.0,
                maximum=8.0,
                average=5.0,
                sample_stddev=1.0,
            )
        ],
        subgroup_rows=[
            {
                "raw_item_name": "P1",
                "subgroup_key": "101",
                "subgroup_identity_complete": 1,
                "subgroup_count": 40,
                "subgroup_stddev": 2.0,
            }
        ],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["CAPABILITY"],
        capability={"rule_code": "CPK_POOLED_WITHIN_RUN_V1"},
    )

    result = _approved_parameter_analysis_service(connection).analyze_parameters(
        request
    )

    capability = result.items[0].parameters[0].capability
    assert capability is not None
    assert capability.status == "ELIGIBLE"
    assert capability.ppk == pytest.approx(5.0 / 3.0)
    assert capability.within_sigma == pytest.approx(2.0)
    assert capability.cpk == pytest.approx(5.0 / 6.0)
    assert capability.subgroup_count == 1
    assert capability.rule_code == "CPK_POOLED_WITHIN_RUN_V1"


def test_parameter_analysis_uses_degree_of_freedom_weighted_pooled_sigma() -> None:
    connection = ParameterAnalysisConnection(
        matched_unit_count=40,
        candidate_measurement_count=40,
        aggregate_rows=[
            _analysis_aggregate_row(
                row_count=40,
                numeric_count=40,
                minimum=2.0,
                maximum=8.0,
                average=5.0,
                sample_stddev=1.0,
            )
        ],
        subgroup_rows=[
            {
                "raw_item_name": "P1",
                "subgroup_key": "101",
                "subgroup_identity_complete": 1,
                "subgroup_count": 10,
                "subgroup_stddev": 1.0,
            },
            {
                "raw_item_name": "P1",
                "subgroup_key": "102",
                "subgroup_identity_complete": 1,
                "subgroup_count": 30,
                "subgroup_stddev": 3.0,
            },
        ],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["CAPABILITY"],
        capability={"rule_code": "CPK_POOLED_WITHIN_RUN_V1"},
    )

    result = _approved_parameter_analysis_service(connection).analyze_parameters(
        request
    )

    capability = result.items[0].parameters[0].capability
    assert capability is not None
    expected_sigma = ((9 * 1.0**2 + 29 * 3.0**2) / (9 + 29)) ** 0.5
    assert capability.within_sigma == pytest.approx(expected_sigma)
    assert capability.cpk == pytest.approx(5.0 / (3.0 * expected_sigma))


def test_parameter_analysis_zero_variance_subgroup_still_contributes_df() -> None:
    connection = ParameterAnalysisConnection(
        matched_unit_count=40,
        candidate_measurement_count=40,
        aggregate_rows=[
            _analysis_aggregate_row(
                row_count=40,
                numeric_count=40,
                minimum=2.0,
                maximum=8.0,
                average=5.0,
                sample_stddev=1.0,
            )
        ],
        subgroup_rows=[
            {
                "raw_item_name": "P1",
                "subgroup_key": "101",
                "subgroup_identity_complete": 1,
                "subgroup_count": 10,
                "subgroup_stddev": 0.0,
            },
            {
                "raw_item_name": "P1",
                "subgroup_key": "102",
                "subgroup_identity_complete": 1,
                "subgroup_count": 30,
                "subgroup_stddev": 3.0,
            },
        ],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["CAPABILITY"],
        capability={"rule_code": "CPK_POOLED_WITHIN_RUN_V1"},
    )

    result = _approved_parameter_analysis_service(connection).analyze_parameters(
        request
    )

    capability = result.items[0].parameters[0].capability
    assert capability is not None
    expected_sigma = ((9 * 0.0**2 + 29 * 3.0**2) / (9 + 29)) ** 0.5
    assert capability.within_sigma == pytest.approx(expected_sigma)
    assert capability.cpk_status == "ELIGIBLE"


@pytest.mark.parametrize(
    ("lsl", "usl", "expected_lower", "expected_upper"),
    [
        (None, 10.0, None, 5.0 / 3.0),
        (0.0, None, 5.0 / 3.0, None),
    ],
    ids=("upper-only", "lower-only"),
)
def test_parameter_analysis_supports_unique_one_sided_formal_spec(
    lsl, usl, expected_lower, expected_upper
) -> None:
    spec_row = ParameterAnalysisConnection._spec_rows(7)[0]
    connection = ParameterAnalysisConnection(
        matched_unit_count=8,
        candidate_measurement_count=8,
        aggregate_rows=[
            _analysis_aggregate_row(
                row_count=8,
                numeric_count=8,
                minimum=2.0,
                maximum=8.0,
                average=5.0,
                sample_stddev=1.0,
            )
        ],
        spec_rows_by_dataset={1: [{**spec_row, "lsl": lsl, "usl": usl}]},
        subgroup_rows=[
            {
                "raw_item_name": "P1",
                "subgroup_key": "101",
                "subgroup_identity_complete": 1,
                "subgroup_count": 8,
                "subgroup_stddev": 2.0,
            }
        ],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["CAPABILITY"],
        capability={"rule_code": "CPK_POOLED_WITHIN_RUN_V1"},
    )

    result = _approved_parameter_analysis_service(connection).analyze_parameters(
        request
    )

    capability = result.items[0].parameters[0].capability
    assert capability is not None
    assert capability.status == "ELIGIBLE"
    if expected_lower is None:
        assert capability.ppl is None
        assert capability.cpl is None
    else:
        assert capability.ppl == pytest.approx(expected_lower)
        assert capability.cpl == pytest.approx(5.0 / 6.0)
    if expected_upper is None:
        assert capability.ppu is None
        assert capability.cpu is None
    else:
        assert capability.ppu == pytest.approx(expected_upper)
        assert capability.cpu == pytest.approx(5.0 / 6.0)
    assert capability.ppk == pytest.approx(5.0 / 3.0)
    assert capability.cpk == pytest.approx(5.0 / 6.0)


@pytest.mark.parametrize(
    ("subgroup_row", "expected_reason"),
    [
        (
            {
                "raw_item_name": "P1",
                "subgroup_key": "LOT-1|",
                "subgroup_identity_complete": 0,
                "subgroup_count": 40,
                "subgroup_stddev": 2.0,
            },
            "CPK_SUBGROUP_IDENTITY_MISSING",
        ),
        (
            {
                "raw_item_name": "P1",
                "subgroup_key": "LOT-1|01",
                "subgroup_identity_complete": 1,
                "subgroup_count": 40,
                "subgroup_stddev": 0.0,
            },
            "CPK_WITHIN_SIGMA_NOT_POSITIVE",
        ),
    ],
    ids=("missing-lot-wafer", "zero-pooled-sigma"),
)
def test_parameter_analysis_lot_wafer_capability_fails_closed(
    subgroup_row, expected_reason: str
) -> None:
    connection = ParameterAnalysisConnection(
        matched_unit_count=40,
        candidate_measurement_count=40,
        aggregate_rows=[
            _analysis_aggregate_row(
                row_count=40,
                numeric_count=40,
                minimum=2.0,
                maximum=8.0,
                average=5.0,
                sample_stddev=1.0,
            )
        ],
        subgroup_rows=[subgroup_row],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["CAPABILITY"],
        capability={"rule_code": "CPK_POOLED_WITHIN_LOT_WAFER_V1"},
    )

    result = _approved_parameter_analysis_service(connection).analyze_parameters(
        request
    )

    capability = result.items[0].parameters[0].capability
    assert capability is not None
    assert capability.ppk_status == "ELIGIBLE"
    assert capability.cpk_status == "NOT_ELIGIBLE"
    assert capability.status == "PARTIAL"
    assert expected_reason in capability.reason_codes
    assert capability.cpk is None


def test_parameter_analysis_censored_values_block_all_capability_indices() -> None:
    connection = ParameterAnalysisConnection(
        matched_unit_count=40,
        candidate_measurement_count=40,
        aggregate_rows=[
            _analysis_aggregate_row(
                row_count=40,
                numeric_count=38,
                measured_count=38,
                over_range=1,
                under_range=1,
                minimum=2.0,
                maximum=8.0,
                average=5.0,
                sample_stddev=1.0,
            )
        ],
        subgroup_rows=[
            {
                "raw_item_name": "P1",
                "subgroup_key": "101",
                "subgroup_identity_complete": 1,
                "subgroup_count": 38,
                "subgroup_stddev": 1.0,
            }
        ],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["CAPABILITY"],
        capability={"rule_code": "CPK_POOLED_WITHIN_RUN_V1"},
    )

    result = _approved_parameter_analysis_service(connection).analyze_parameters(
        request
    )

    capability = result.items[0].parameters[0].capability
    assert capability is not None
    assert capability.status == "NOT_ELIGIBLE"
    assert capability.ppk is None
    assert capability.cpk is None
    assert "CENSORED_MEASUREMENTS_PRESENT" in capability.reason_codes


def test_parameter_analysis_rejects_multiple_formal_bindings_without_choosing_first() -> (
    None
):
    spec_rows = ParameterAnalysisConnection._spec_rows(7)
    spec_rows.append({**spec_rows[0], "spec_set_id": 8})
    connection = ParameterAnalysisConnection(
        spec_rows_by_dataset={1: spec_rows},
        subgroup_rows=[
            {
                "raw_item_name": "P1",
                "subgroup_key": "101",
                "subgroup_identity_complete": 1,
                "subgroup_count": 8,
                "subgroup_stddev": 2.0,
            }
        ],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["CAPABILITY"],
        capability={"rule_code": "CPK_POOLED_WITHIN_RUN_V1"},
    )

    result = _approved_parameter_analysis_service(connection).analyze_parameters(
        request
    )

    capability = result.items[0].parameters[0].capability
    assert capability is not None
    assert capability.status == "NOT_ELIGIBLE"
    assert "SPEC_CONTEXT_AMBIGUOUS" in capability.reason_codes
    assert capability.ppk is None
    assert capability.cpk is None


def test_parameter_analysis_rejects_reversed_formal_limits() -> None:
    spec_row = ParameterAnalysisConnection._spec_rows(7)[0]
    connection = ParameterAnalysisConnection(
        spec_rows_by_dataset={1: [{**spec_row, "lsl": 11.0, "usl": 10.0}]},
        subgroup_rows=[
            {
                "raw_item_name": "P1",
                "subgroup_key": "101",
                "subgroup_identity_complete": 1,
                "subgroup_count": 8,
                "subgroup_stddev": 2.0,
            }
        ],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["CAPABILITY"],
        capability={"rule_code": "CPK_POOLED_WITHIN_RUN_V1"},
    )

    with pytest.raises(DomainError) as error:
        _approved_parameter_analysis_service(connection).analyze_parameters(request)

    assert error.value.code == "ANALYSIS_SPEC_CONTRACT_INVALID"


def test_parameter_analysis_rejects_missing_parameter_even_for_one_dataset() -> None:
    connection = ParameterAnalysisConnection(identity_rows_by_dataset={1: []})
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
    )

    with pytest.raises(DomainError) as error:
        _approved_parameter_analysis_service(connection).analyze_parameters(request)

    assert error.value.code == "ANALYSIS_PARAMETER_INCOMPATIBLE"


def test_parameter_analysis_rejects_duplicate_raw_name_steps_in_one_program() -> None:
    first = ParameterAnalysisConnection._identity_rows()[0]
    connection = ParameterAnalysisConnection(
        identity_rows_by_dataset={
            1: [
                first,
                {
                    **first,
                    "step_code": "STEP_P1_DUPLICATE",
                    "sequence_no": 2,
                },
            ]
        }
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).analyze_parameters(request)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_PARAMETER_INCOMPATIBLE"
    assert not any("AS status_measured" in sql for sql, _ in connection.executed)


def test_parameter_analysis_rejects_other_stage_before_statistics() -> None:
    connection = ParameterAnalysisConnection(stage_by_dataset={1: "OTHER"})
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).analyze_parameters(request)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_STAGE_INCOMPATIBLE"
    assert not any("AS status_measured" in sql for sql, _ in connection.executed)


@pytest.mark.parametrize(
    "connection",
    [
        ParameterAnalysisConnection(status_by_dataset={1: "VALIDATING"}),
        ParameterAnalysisConnection(current_by_dataset={1: False}),
    ],
    ids=("not-published", "not-current"),
)
def test_parameter_analysis_requires_current_published_version(connection) -> None:
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).analyze_parameters(request)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_VERSION_NOT_CURRENT"
    assert not any("AS status_measured" in sql for sql, _ in connection.executed)


@pytest.mark.parametrize(
    ("connection", "expected_code"),
    [
        (
            ParameterAnalysisConnection(stage_by_dataset={1: "CP", 2: "FT"}),
            "ANALYSIS_STAGE_INCOMPATIBLE",
        ),
        (
            ParameterAnalysisConnection(
                stage_by_dataset={1: "CP", 2: "CP"},
                context_spec_by_dataset={1: 7, 2: 8},
            ),
            "ANALYSIS_SPEC_INCOMPATIBLE",
        ),
    ],
    ids=("stage", "cp-spec-context"),
)
def test_multi_dataset_parameter_analysis_applies_compatibility_gates(
    connection, expected_code: str
) -> None:
    request = DatasetParameterAnalysisRequest(
        datasets=[
            {"dataset_id": 1, "version_no": 1},
            {"dataset_id": 2, "version_no": 1},
        ],
        parameters=["P1"],
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).analyze_parameters(request)  # type: ignore[arg-type]

    assert error.value.code == expected_code
    assert not any("AS status_measured" in sql for sql, _ in connection.executed)


def test_multi_dataset_parameter_analysis_rejects_step_identity_conflict() -> None:
    first = ParameterAnalysisConnection._identity_rows()[0]
    connection = ParameterAnalysisConnection(
        identity_rows_by_dataset={
            1: [first],
            2: [{**first, "program_version_id": 202, "step_code": "OTHER_STEP"}],
        }
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[
            {"dataset_id": 1, "version_no": 1},
            {"dataset_id": 2, "version_no": 1},
        ],
        parameters=["P1"],
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).analyze_parameters(request)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_PARAMETER_INCOMPATIBLE"


def test_multi_dataset_parameter_analysis_rejects_canonical_identity_conflict() -> None:
    first = ParameterAnalysisConnection._identity_rows()[0]
    connection = ParameterAnalysisConnection(
        identity_rows_by_dataset={
            1: [first],
            2: [
                {
                    **first,
                    "program_version_id": 202,
                    "canonical_parameter_code": "P1_ALTERNATE",
                }
            ],
        }
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[
            {"dataset_id": 1, "version_no": 1},
            {"dataset_id": 2, "version_no": 1},
        ],
        parameters=["P1"],
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).analyze_parameters(request)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_PARAMETER_INCOMPATIBLE"
    assert not any("AS status_measured" in sql for sql, _ in connection.executed)


def test_multi_dataset_parameter_analysis_rejects_sequence_identity_conflict() -> None:
    first = ParameterAnalysisConnection._identity_rows()[0]
    connection = ParameterAnalysisConnection(
        identity_rows_by_dataset={
            1: [first],
            2: [
                {
                    **first,
                    "program_version_id": 202,
                    "sequence_no": 2,
                }
            ],
        }
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[
            {"dataset_id": 1, "version_no": 1},
            {"dataset_id": 2, "version_no": 1},
        ],
        parameters=["P1"],
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).analyze_parameters(request)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_PARAMETER_INCOMPATIBLE"
    assert not any("AS status_measured" in sql for sql, _ in connection.executed)


def test_multi_dataset_capability_rejects_formal_spec_signature_conflict() -> None:
    second_spec = ParameterAnalysisConnection._spec_rows(72)[0]
    second_spec = {**second_spec, "usl": 9.0}
    connection = ParameterAnalysisConnection(
        stage_by_dataset={1: "FT", 2: "FT"},
        context_spec_by_dataset={1: None, 2: None},
        spec_rows_by_dataset={
            1: ParameterAnalysisConnection._spec_rows(71),
            2: [second_spec],
        },
        subgroup_rows=[
            {
                "raw_item_name": "P1",
                "subgroup_key": "101",
                "subgroup_identity_complete": 1,
                "subgroup_count": 8,
                "subgroup_stddev": 2.0,
            }
        ],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[
            {"dataset_id": 1, "version_no": 1},
            {"dataset_id": 2, "version_no": 1},
        ],
        parameters=["P1"],
        analyses=["CAPABILITY"],
        capability={"rule_code": "CPK_POOLED_WITHIN_RUN_V1"},
    )

    with pytest.raises(DomainError) as error:
        _approved_parameter_analysis_service(connection).analyze_parameters(request)

    assert error.value.code == "ANALYSIS_SPEC_INCOMPATIBLE"


def test_parameter_analysis_rejects_workloads_above_bounded_limit() -> None:
    connection = ParameterAnalysisConnection(candidate_measurement_count=2_000_001)
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).analyze_parameters(request)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_WORKLOAD_LIMIT_EXCEEDED"
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    ("analyses", "connection_kwargs"),
    [
        (["BOX_PLOT"], {"box_rows": []}),
        (["HISTOGRAM"], {"histogram_rows": []}),
    ],
    ids=("box", "histogram"),
)
def test_parameter_analysis_rejects_missing_requested_aggregates(
    analyses, connection_kwargs
) -> None:
    connection = ParameterAnalysisConnection(**connection_kwargs)
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=analyses,
    )

    with pytest.raises(DomainError) as error:
        _approved_parameter_analysis_service(connection).analyze_parameters(request)

    assert error.value.code == "ANALYSIS_AGGREGATE_CONTRACT_INVALID"


@pytest.mark.parametrize(
    ("analysis", "rule_code"),
    [
        ("BOX_PLOT", "TUKEY_1_5_IQR_PERCENTILE_CONT_LINEAR_V1"),
        ("HISTOGRAM", "EQUAL_WIDTH_FIXED_BINS_LAST_CLOSED_V1"),
    ],
    ids=("box-plot", "histogram"),
)
def test_parameter_analysis_owner_gate_rejects_unapproved_fixed_method_before_sql(
    analysis: str, rule_code: str
) -> None:
    connection = ParameterAnalysisConnection()
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=[analysis],
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).analyze_parameters(request)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_RULE_NOT_APPROVED"
    assert error.value.status_code == 409
    assert error.value.details == [{"rule_code": rule_code}]
    assert connection.executed == []


def test_parameter_analysis_owner_gate_rejects_unapproved_rule_before_sql() -> None:
    connection = ParameterAnalysisConnection()
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["CAPABILITY"],
        capability={"rule_code": "CPK_POOLED_WITHIN_RUN_V1"},
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).analyze_parameters(request)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_RULE_NOT_APPROVED"
    assert connection.executed == []


def test_parameter_analysis_rejects_partial_formal_spec_scope_coverage() -> None:
    covered = ParameterAnalysisConnection._spec_rows(7)[0]
    uncovered = {
        **covered,
        "run_id": 102,
        "spec_set_id": None,
        "spec_item_id": None,
        "unit_code": None,
        "lsl": None,
        "usl": None,
        "condition_json": None,
    }
    connection = ParameterAnalysisConnection(
        spec_rows_by_dataset={1: [covered, uncovered]},
        subgroup_rows=[
            {
                "raw_item_name": "P1",
                "subgroup_key": "101",
                "subgroup_identity_complete": 1,
                "subgroup_count": 8,
                "subgroup_stddev": 2.0,
            }
        ],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["CAPABILITY"],
        capability={"rule_code": "CPK_POOLED_WITHIN_RUN_V1"},
    )

    result = _approved_parameter_analysis_service(connection).analyze_parameters(
        request
    )

    parameter = result.items[0].parameters[0]
    capability = parameter.capability
    assert capability is not None
    assert capability.status == "NOT_ELIGIBLE"
    assert "FORMAL_SPEC_SCOPE_NOT_COVERED" in capability.reason_codes
    assert capability.lsl is None
    assert capability.usl is None
    assert capability.ppk is None
    assert capability.cpk is None
    assert parameter.identity.limit_source == "UNRESOLVED"
    assert parameter.identity.spec_set_ids == ()


def test_multi_dataset_parameter_analysis_rejects_bias_identity_conflict() -> None:
    first = ParameterAnalysisConnection._identity_rows()[0]
    connection = ParameterAnalysisConnection(
        identity_rows_by_dataset={
            1: [{**first, "condition_json": '{"text":"VGS=0V","bias1":"1V"}'}],
            2: [
                {
                    **first,
                    "run_id": 201,
                    "run_program_version_id": 202,
                    "test_item_id": 302,
                    "program_version_id": 202,
                    "condition_json": '{"bias1":"2V","text":"VGS=0V"}',
                }
            ],
        }
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[
            {"dataset_id": 1, "version_no": 1},
            {"dataset_id": 2, "version_no": 1},
        ],
        parameters=["P1"],
    )

    with pytest.raises(DomainError) as error:
        SqlDatasetService(Engine(connection)).analyze_parameters(request)  # type: ignore[arg-type]

    assert error.value.code == "ANALYSIS_PARAMETER_INCOMPATIBLE"
    assert not any("AS status_measured" in sql for sql, _ in connection.executed)


def test_parameter_analysis_formal_condition_mismatch_hides_released_limits() -> None:
    identity = ParameterAnalysisConnection._identity_rows()[0]
    formal = ParameterAnalysisConnection._spec_rows(7)[0]
    connection = ParameterAnalysisConnection(
        identity_rows_by_dataset={
            1: [{**identity, "condition_json": '{"text":"VGS=0V","bias1":"1V"}'}]
        },
        spec_rows_by_dataset={
            1: [{**formal, "condition_json": '{"bias1":"2V","text":"VGS=0V"}'}]
        },
        subgroup_rows=[
            {
                "raw_item_name": "P1",
                "subgroup_key": "101",
                "subgroup_identity_complete": 1,
                "subgroup_count": 8,
                "subgroup_stddev": 2.0,
            }
        ],
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["CAPABILITY"],
        capability={"rule_code": "CPK_POOLED_WITHIN_RUN_V1"},
    )

    result = _approved_parameter_analysis_service(connection).analyze_parameters(
        request
    )

    parameter = result.items[0].parameters[0]
    capability = parameter.capability
    assert capability is not None
    assert "SPEC_CONTEXT_AMBIGUOUS" in capability.reason_codes
    assert capability.lsl is None
    assert capability.usl is None
    assert capability.ppk is None
    assert capability.cpk is None
    assert parameter.identity.limit_source == "UNRESOLVED"
    assert parameter.identity.spec_set_ids == ()


def test_parameter_analysis_all_statistics_bind_exact_resolved_test_item_id() -> None:
    connection = ParameterAnalysisConnection(
        subgroup_rows=[
            {
                "raw_item_name": "P1",
                "subgroup_key": "101",
                "subgroup_identity_complete": 1,
                "subgroup_count": 8,
                "subgroup_stddev": 2.0,
            }
        ]
    )
    request = DatasetParameterAnalysisRequest(
        datasets=[{"dataset_id": 1, "version_no": 1}],
        parameters=["P1"],
        analyses=["DESCRIPTIVE", "BOX_PLOT", "HISTOGRAM", "CAPABILITY"],
        capability={"rule_code": "CPK_POOLED_WITHIN_RUN_V1"},
    )

    _approved_parameter_analysis_service(connection).analyze_parameters(request)

    measurement_queries = [
        (sql, parameters)
        for sql, parameters in connection.executed
        if "JOIN test.measurement m" in sql
    ]
    assert len(measurement_queries) == 6
    assert all(
        parameters["analysis_test_item_ids"] == (301,)
        for _, parameters in measurement_queries
    )
    assert all(
        "m.test_item_id IN" in sql and "analysis_test_item_ids" in sql
        for sql, _ in measurement_queries
    )
    assert all(
        "tid.raw_item_name IN :analysis_parameters" not in sql
        for sql, _ in measurement_queries
    )
