from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from app.core.errors import DomainError
from app.domain.analytics import AnalyticsCapability
from app.domain.wafer_summary import (
    WaferParameterSummary,
    WaferSummaryDrilldownContext,
    WaferSummaryRequest,
    WaferSummaryResult,
    WaferSummaryRow,
    WaferSummarySort,
    WaferSummarySortDirection,
)
from app.infrastructure.sql_analytics_service import (
    SqlAnalyticsService,
    _condition_text,
    _finite_float,
    _hashes,
)

_CONTRACT_VERSION = "ANALYTICS_WAFER_SUMMARY_V1"


def _statement(sql: str, expanding: tuple[str, ...] = ()):
    statement = text(sql)
    if expanding:
        statement = statement.bindparams(
            *(bindparam(name, expanding=True) for name in expanding)
        )
    return statement


def _identity_signature(row: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        str(row["canonical_parameter_code"] or ""),
        str(row["step_code"]),
        int(row["sequence_no"]),
        str(row["unit_code"] or ""),
        _finite_float(row["program_lsl"], field="program LSL"),
        _finite_float(row["program_usl"], field="program USL"),
        _condition_text(row["condition_json"]),
    )


class SqlWaferSummaryService:
    """Read-only wafer-level delivery table with server-owned paging semantics."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._analytics = SqlAnalyticsService(engine)

    @staticmethod
    def _sort_rows(
        rows: list[WaferSummaryRow], request: WaferSummaryRequest
    ) -> list[WaferSummaryRow]:
        def identity(item: WaferSummaryRow) -> tuple[int, int, str, str]:
            return (
                item.dataset_id,
                item.version_no,
                item.lot_id,
                item.wafer_id,
            )

        if request.sort_by == WaferSummarySort.DATASET:
            key = identity
        elif request.sort_by == WaferSummarySort.LOT:

            def lot_key(item: WaferSummaryRow) -> tuple[object, ...]:
                return (item.lot_id, item.wafer_id, *identity(item))

            key = lot_key
        elif request.sort_by == WaferSummarySort.WAFER:

            def wafer_key(item: WaferSummaryRow) -> tuple[object, ...]:
                return (item.wafer_id, item.lot_id, *identity(item))

            key = wafer_key
        elif request.sort_by == WaferSummarySort.UNIT_COUNT:
            factor = (
                -1 if request.sort_direction == WaferSummarySortDirection.DESC else 1
            )

            def unit_count_key(item: WaferSummaryRow) -> tuple[object, ...]:
                return (factor * item.unit_count, *identity(item))

            return sorted(rows, key=unit_count_key)
        else:
            factor = (
                -1 if request.sort_direction == WaferSummarySortDirection.DESC else 1
            )

            def yield_key(item: WaferSummaryRow) -> tuple[object, ...]:
                return (
                    item.yield_rate is None,
                    factor * (item.yield_rate or 0.0),
                    *identity(item),
                )

            return sorted(rows, key=yield_key)
        return sorted(
            rows,
            key=key,
            reverse=request.sort_direction == WaferSummarySortDirection.DESC,
        )

    @staticmethod
    def _order_by_sql(request: WaferSummaryRequest) -> str:
        direction = request.sort_direction.value
        identity_asc = "dataset_id ASC,version_no ASC,lot_id ASC,wafer_id ASC"
        if request.sort_by == WaferSummarySort.DATASET:
            return (
                f"dataset_id {direction},version_no {direction},"
                f"lot_id {direction},wafer_id {direction}"
            )
        if request.sort_by == WaferSummarySort.LOT:
            return (
                f"lot_id {direction},wafer_id {direction},"
                f"dataset_id {direction},version_no {direction}"
            )
        if request.sort_by == WaferSummarySort.WAFER:
            return (
                f"wafer_id {direction},lot_id {direction},"
                f"dataset_id {direction},version_no {direction}"
            )
        if request.sort_by == WaferSummarySort.UNIT_COUNT:
            return f"unit_count {direction},{identity_asc}"
        return (
            "CASE WHEN yield_rate IS NULL THEN 1 ELSE 0 END ASC,"
            f"yield_rate {direction},{identity_asc}"
        )

    @staticmethod
    def _selected_context_cte(
        context_rows: tuple[Mapping[str, Any], ...],
    ) -> tuple[str, dict[str, object]]:
        selects: list[str] = []
        parameters: dict[str, object] = {}
        for index, row in enumerate(context_rows):
            selects.append(
                f"SELECT :context_dataset_{index} AS dataset_id,"
                f":context_version_{index} AS version_no"
            )
            parameters[f"context_dataset_{index}"] = int(row["dataset_id"])
            parameters[f"context_version_{index}"] = int(row["version_no"])
        return (
            "selected_contexts(dataset_id,version_no) AS ("
            + " UNION ALL ".join(selects)
            + ")",
            parameters,
        )

    @staticmethod
    def _page_wafers_cte(
        rows: tuple[Mapping[str, Any], ...],
    ) -> tuple[str, dict[str, object]]:
        """Bind one bounded page as a set instead of one UNION branch per wafer."""

        values: list[str] = []
        parameters: dict[str, object] = {}
        for index, row in enumerate(rows):
            values.append(
                f"(:page_dataset_{index},:page_version_{index},"
                f":page_lot_{index},:page_wafer_{index})"
            )
            parameters[f"page_dataset_{index}"] = int(row["dataset_id"])
            parameters[f"page_version_{index}"] = int(row["version_no"])
            parameters[f"page_lot_{index}"] = str(row["lot_id"])
            parameters[f"page_wafer_{index}"] = str(row["wafer_id"])
        return (
            "page_wafers(dataset_id,version_no,lot_id,wafer_id) AS ("
            "SELECT page_values.dataset_id,page_values.version_no,"
            "page_values.lot_id,page_values.wafer_id FROM (VALUES "
            + ",".join(values)
            + ") page_values(dataset_id,version_no,lot_id,wafer_id))",
            parameters,
        )

    @staticmethod
    def _parameter_id_batches(
        parameters: tuple[str, ...] | list[str],
        parameter_ids_by_name: Mapping[str, set[int]],
    ) -> tuple[tuple[str, tuple[int, ...]], ...]:
        """Keep each exact parameter in a bounded seek-friendly SQL batch."""

        return tuple(
            (parameter, tuple(sorted(parameter_ids_by_name[parameter])))
            for parameter in parameters
        )

    def summarize(self, request: WaferSummaryRequest) -> WaferSummaryResult:
        page_rows: tuple[Mapping[str, Any], ...] = ()
        parameter_aggregates: dict[
            tuple[int, int, str, str, str], Mapping[str, Any]
        ] = {}
        program_signatures: dict[str, set[tuple[object, ...]]] = defaultdict(set)
        formal_signatures: dict[str, set[tuple[object, ...]]] = defaultdict(set)
        parameter_ids_by_name: dict[str, set[int]] = defaultdict(set)
        total = 0
        with self._engine.connect() as connection:
            context_rows = self._analytics._context_rows(connection, request)
            dataset_context = self._analytics._dataset_context(context_rows)
            if dataset_context.test_stage != "CP":
                raise DomainError(
                    "ANALYSIS_STAGE_INCOMPATIBLE",
                    "wafer summary is only available for CP datasets",
                    409,
                )
            rule_context = self._analytics._rule_context(
                connection, context_rows, request
            )
            all_source_run_ids: set[int] = set()
            all_condition_item_ids: set[int] = set()
            for context in context_rows:
                source_rows = self._analytics._source_rows(connection, context)
                item_rows = self._analytics._item_rows(connection, context)
                source_run_ids = self._analytics._selected_run_ids(request, source_rows)
                condition_item_ids = self._analytics._selected_condition_item_ids(
                    request, item_rows
                )
                if source_run_ids is not None:
                    all_source_run_ids.update(source_run_ids)
                if condition_item_ids is not None:
                    all_condition_item_ids.update(condition_item_ids)
                if request.parameters:
                    parameter_ids = self._analytics._parameter_ids(
                        item_rows, tuple(request.parameters)
                    )
                    selected = set(request.parameters)
                    item_by_id = {
                        int(item["test_item_id"]): item
                        for item in item_rows
                        if str(item["raw_item_name"]) in selected
                    }
                    for item in item_by_id.values():
                        parameter_name = str(item["raw_item_name"])
                        program_signatures[parameter_name].add(
                            _identity_signature(item)
                        )
                        parameter_ids_by_name[parameter_name].add(
                            int(item["test_item_id"])
                        )
                    if context["spec_set_id"] is None:
                        raise DomainError(
                            "ANALYSIS_SPEC_MISSING",
                            "wafer summary parameter columns require a released formal Spec",
                            409,
                        )
                    spec_rows = (
                        connection.execute(
                            _statement(
                                "SELECT tid.test_item_id,tid.raw_item_name,tid.unit_code AS program_unit,"
                                "tid.condition_json AS program_condition,ss.spec_set_id,"
                                "ss.version_code,si.spec_item_id,si.unit_code AS spec_unit,"
                                "si.lsl,si.usl,si.lower_operator,si.upper_operator,"
                                "si.condition_json AS spec_condition "
                                "FROM mdm.test_item_definition tid "
                                "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=:spec_set_id "
                                "AND ss.status='RELEASED' "
                                "LEFT JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
                                "AND si.test_item_id=tid.test_item_id "
                                "WHERE tid.test_item_id IN :parameter_ids "
                                "ORDER BY tid.test_item_id,si.spec_item_id",
                                ("parameter_ids",),
                            ),
                            {
                                "spec_set_id": int(context["spec_set_id"]),
                                "parameter_ids": parameter_ids,
                            },
                        )
                        .mappings()
                        .all()
                    )
                    by_item: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
                    for row in spec_rows:
                        by_item[int(row["test_item_id"])].append(row)
                    for item_id, item in item_by_id.items():
                        candidates = [
                            row
                            for row in by_item.get(item_id, [])
                            if row["spec_set_id"] is not None
                            and row["spec_item_id"] is not None
                        ]
                        if len(candidates) != 1:
                            raise DomainError(
                                "ANALYSIS_SPEC_INCOMPATIBLE",
                                "wafer summary formal Spec is missing or ambiguous",
                                409,
                                details=[{"test_item_id": item_id}],
                            )
                        spec = candidates[0]
                        program_unit = str(item["unit_code"] or "")
                        spec_unit = str(spec["spec_unit"] or "")
                        program_condition = _condition_text(item["condition_json"])
                        spec_condition = _condition_text(spec["spec_condition"])
                        lsl = _finite_float(spec["lsl"], field="formal LSL")
                        usl = _finite_float(spec["usl"], field="formal USL")
                        if (
                            program_unit != spec_unit
                            or program_condition != spec_condition
                            or (lsl is None and usl is None)
                            or (lsl is not None and usl is not None and lsl > usl)
                            or spec["lower_operator"] not in {None, ">", ">="}
                            or spec["upper_operator"] not in {None, "<", "<="}
                        ):
                            raise DomainError(
                                "ANALYSIS_SPEC_INCOMPATIBLE",
                                "wafer summary formal Spec identity, limits or operators are invalid",
                                409,
                                details=[{"test_item_id": item_id}],
                            )
                        formal_signatures[str(item["raw_item_name"])].add(
                            (spec_unit, lsl, usl, spec_condition)
                        )

            incompatible = sorted(
                parameter
                for parameter in request.parameters
                if len(program_signatures.get(parameter, set())) != 1
                or len(formal_signatures.get(parameter, set())) != 1
            )
            if incompatible:
                raise DomainError(
                    "ANALYSIS_PARAMETER_INCOMPATIBLE",
                    "wafer summary parameters lack one compatible formal identity and Spec",
                    409,
                    details=[{"parameters": incompatible}],
                )

            source_filter = (
                tuple(sorted(all_source_run_ids))
                if request.filters.source_ids
                else None
            )
            condition_filter = (
                tuple(sorted(all_condition_item_ids))
                if request.filters.test_conditions
                else None
            )
            filter_sql, filter_parameters, expanding = self._analytics._filter_sql(
                request,
                source_run_ids=source_filter,
                condition_item_ids=condition_filter,
            )
            context_cte, context_parameters = self._selected_context_cte(context_rows)
            base_join = self._analytics._base_join()
            wafer_cte = (
                "wafer_agg AS (SELECT dv.dataset_id,dv.version_no,tr.lot_id,"
                "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                "COUNT_BIG(*) AS unit_count,"
                "SUM(CASE WHEN ur.overall_result='PASS' THEN CONVERT(bigint,1) ELSE 0 END) AS pass_count,"
                "SUM(CASE WHEN ur.overall_result='FAIL' THEN CONVERT(bigint,1) ELSE 0 END) AS fail_count,"
                "SUM(CASE WHEN ur.overall_result='UNKNOWN' THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_count,"
                "SUM(CASE WHEN ur.overall_result='ABORT' THEN CONVERT(bigint,1) ELSE 0 END) AS abort_count,"
                "SUM(CASE WHEN ur.overall_result IN('PASS','FAIL') THEN CONVERT(bigint,1) ELSE 0 END) AS known_yield_denominator,"
                "CONVERT(float,SUM(CASE WHEN ur.overall_result='PASS' THEN CONVERT(bigint,1) ELSE 0 END))/"
                "NULLIF(CONVERT(float,SUM(CASE WHEN ur.overall_result IN('PASS','FAIL') THEN CONVERT(bigint,1) ELSE 0 END)),0.0) AS yield_rate"
                + base_join
                + "JOIN selected_contexts sc ON sc.dataset_id=dv.dataset_id "
                "AND sc.version_no=dv.version_no WHERE 1=1"
                + filter_sql
                + " GROUP BY dv.dataset_id,dv.version_no,tr.lot_id,"
                "COALESCE(ur.wafer_id,tr.wafer_id))"
            )
            common_parameters = {
                **context_parameters,
                **filter_parameters,
            }
            page_rows = tuple(
                connection.execute(
                    _statement(
                        "WITH "
                        + context_cte
                        + ","
                        + wafer_cte
                        + " SELECT dataset_id,version_no,lot_id,wafer_id,unit_count,"
                        "pass_count,fail_count,unknown_count,abort_count,"
                        "known_yield_denominator,yield_rate,"
                        "COUNT_BIG(*) OVER() AS total,"
                        "SUM(CASE WHEN lot_id IS NULL OR wafer_id IS NULL "
                        "THEN CONVERT(bigint,1) ELSE 0 END) OVER() "
                        "AS invalid_identity_count FROM wafer_agg ORDER BY "
                        + self._order_by_sql(request)
                        + " OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY",
                        expanding,
                    ),
                    {
                        **common_parameters,
                        "offset": (request.page - 1) * request.page_size,
                        "page_size": request.page_size,
                    },
                )
                .mappings()
                .all()
            )
            if page_rows:
                totals = {int(row["total"] or 0) for row in page_rows}
                invalid_identity_counts = {
                    int(row["invalid_identity_count"] or 0) for row in page_rows
                }
                if len(totals) != 1 or len(invalid_identity_counts) != 1:
                    raise DomainError(
                        "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                        "wafer page totals do not reconcile",
                        409,
                    )
                total = totals.pop()
                invalid_identity_count = invalid_identity_counts.pop()
            else:
                count_row = (
                    connection.execute(
                        _statement(
                            "WITH "
                            + context_cte
                            + ","
                            + wafer_cte
                            + " SELECT COUNT_BIG(*) AS total,"
                            "SUM(CASE WHEN lot_id IS NULL OR wafer_id IS NULL "
                            "THEN CONVERT(bigint,1) ELSE 0 END) "
                            "AS invalid_identity_count FROM wafer_agg",
                            expanding,
                        ),
                        common_parameters,
                    )
                    .mappings()
                    .one()
                )
                total = int(count_row["total"] or 0)
                invalid_identity_count = int(count_row["invalid_identity_count"] or 0)
            if invalid_identity_count:
                raise DomainError(
                    "ANALYSIS_WAFER_IDENTITY_REQUIRED",
                    "wafer summary requires non-null Lot and Wafer identity",
                    409,
                )

            if request.parameters and page_rows:
                page_cte, page_parameters = self._page_wafers_cte(page_rows)
                parameter_statement = _statement(
                    "WITH "
                    + context_cte
                    + ","
                    + page_cte
                    + " SELECT dv.dataset_id,dv.version_no,tr.lot_id,"
                    "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                    "tid.raw_item_name,MAX(si.unit_code) AS unit_code,"
                    "SUM(CASE WHEN m.value_numeric IS NOT NULL AND "
                    "m.measurement_status NOT IN('MISSING','NOT_TESTED','INVALID') "
                    "THEN CONVERT(bigint,1) ELSE 0 END) AS measured_count,"
                    "SUM(CASE WHEN m.value_numeric IS NOT NULL AND "
                    "m.measurement_status NOT IN('MISSING','NOT_TESTED','INVALID') AND "
                    "((si.lsl IS NOT NULL AND ((COALESCE(si.lower_operator,N'>=')=N'>' AND m.value_numeric<=si.lsl) "
                    "OR (COALESCE(si.lower_operator,N'>=')=N'>=' AND m.value_numeric<si.lsl))) OR "
                    "(si.usl IS NOT NULL AND ((COALESCE(si.upper_operator,N'<=')=N'<' AND m.value_numeric>=si.usl) "
                    "OR (COALESCE(si.upper_operator,N'<=')=N'<=' AND m.value_numeric>si.usl)))) "
                    "THEN CONVERT(bigint,1) ELSE 0 END) AS out_of_spec_count,"
                    "MIN(CASE WHEN m.measurement_status NOT IN('MISSING','NOT_TESTED','INVALID') "
                    "THEN m.value_numeric END) AS minimum,"
                    "MAX(CASE WHEN m.measurement_status NOT IN('MISSING','NOT_TESTED','INVALID') "
                    "THEN m.value_numeric END) AS maximum,"
                    "AVG(CASE WHEN m.measurement_status NOT IN('MISSING','NOT_TESTED','INVALID') "
                    "THEN CONVERT(float,m.value_numeric) END) AS mean"
                    + base_join
                    + "JOIN selected_contexts sc ON sc.dataset_id=dv.dataset_id "
                    "AND sc.version_no=dv.version_no "
                    "JOIN page_wafers pw ON pw.dataset_id=dv.dataset_id "
                    "AND pw.version_no=dv.version_no AND pw.lot_id=tr.lot_id "
                    "AND pw.wafer_id=COALESCE(ur.wafer_id,tr.wafer_id) "
                    "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                    "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                    "JOIN mdm.spec_set ss ON ss.spec_set_id=dv.spec_set_id "
                    "AND ss.status='RELEASED' "
                    "JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
                    "AND si.test_item_id=m.test_item_id WHERE 1=1"
                    + filter_sql
                    + " AND m.test_item_id IN :parameter_ids "
                    "GROUP BY dv.dataset_id,dv.version_no,tr.lot_id,"
                    "COALESCE(ur.wafer_id,tr.wafer_id),tid.raw_item_name",
                    expanding + ("parameter_ids",),
                )
                for parameter, parameter_ids in self._parameter_id_batches(
                    request.parameters, parameter_ids_by_name
                ):
                    parameter_values = (
                        connection.execute(
                            parameter_statement,
                            {
                                **common_parameters,
                                **page_parameters,
                                "parameter_ids": parameter_ids,
                            },
                        )
                        .mappings()
                        .all()
                    )
                    for row in parameter_values:
                        parameter_aggregates[
                            (
                                int(row["dataset_id"]),
                                int(row["version_no"]),
                                str(row["lot_id"]),
                                str(row["wafer_id"]),
                                str(row["raw_item_name"]),
                            )
                        ] = row

        rows: list[WaferSummaryRow] = []
        for raw_summary in page_rows:
            summary = {
                "dataset_id": int(raw_summary["dataset_id"]),
                "version_no": int(raw_summary["version_no"]),
                "lot_id": str(raw_summary["lot_id"]),
                "wafer_id": str(raw_summary["wafer_id"]),
                "unit_count": int(raw_summary["unit_count"] or 0),
                "pass_count": int(raw_summary["pass_count"] or 0),
                "fail_count": int(raw_summary["fail_count"] or 0),
                "unknown_count": int(raw_summary["unknown_count"] or 0),
                "abort_count": int(raw_summary["abort_count"] or 0),
            }
            known = int(raw_summary["known_yield_denominator"] or 0)
            if (
                summary["pass_count"]
                + summary["fail_count"]
                + summary["unknown_count"]
                + summary["abort_count"]
                != summary["unit_count"]
                or known != summary["pass_count"] + summary["fail_count"]
            ):
                raise DomainError(
                    "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                    "wafer result counts do not reconcile",
                    409,
                )
            parameter_summaries: list[WaferParameterSummary] = []
            for parameter in request.parameters:
                aggregate = parameter_aggregates.get(
                    (
                        summary["dataset_id"],
                        summary["version_no"],
                        summary["lot_id"],
                        summary["wafer_id"],
                        parameter,
                    )
                )
                measured = int(aggregate["measured_count"] or 0) if aggregate else 0
                if measured > summary["unit_count"]:
                    raise DomainError(
                        "ANALYSIS_PARAMETER_INCOMPATIBLE",
                        "wafer parameter resolves to more than one valid measurement per unit",
                        409,
                        details=[
                            {
                                "dataset_id": summary["dataset_id"],
                                "lot_id": summary["lot_id"],
                                "wafer_id": summary["wafer_id"],
                                "parameter": parameter,
                            }
                        ],
                    )
                parameter_summaries.append(
                    WaferParameterSummary(
                        parameter=parameter,
                        unit=(
                            str(aggregate["unit_code"])
                            if aggregate and aggregate["unit_code"] is not None
                            else None
                        ),
                        measured_count=measured,
                        missing_count=summary["unit_count"] - measured,
                        out_of_spec_count=(
                            int(aggregate["out_of_spec_count"] or 0) if aggregate else 0
                        ),
                        minimum=(
                            _finite_float(aggregate["minimum"], field="wafer minimum")
                            if aggregate
                            else None
                        ),
                        maximum=(
                            _finite_float(aggregate["maximum"], field="wafer maximum")
                            if aggregate
                            else None
                        ),
                        mean=(
                            _finite_float(aggregate["mean"], field="wafer mean")
                            if aggregate
                            else None
                        ),
                    )
                )
            rows.append(
                WaferSummaryRow(
                    **summary,
                    known_yield_denominator=known,
                    yield_rate=(
                        _finite_float(raw_summary["yield_rate"], field="wafer yield")
                        if known
                        else None
                    ),
                    parameters=tuple(parameter_summaries),
                    drilldown_context=WaferSummaryDrilldownContext(
                        dataset_id=summary["dataset_id"],
                        version_no=summary["version_no"],
                        lot_id=summary["lot_id"],
                        wafer_id=summary["wafer_id"],
                    ),
                )
            )
        items = tuple(rows)
        warnings = (
            ("YIELD_DENOMINATOR_EMPTY",)
            if any(item.known_yield_denominator == 0 for item in items)
            else ()
        )
        return WaferSummaryResult(
            contract_version=_CONTRACT_VERSION,
            dataset_context=dataset_context,
            filter_summary=_hashes(request),
            rule_context=rule_context,
            capabilities=(
                AnalyticsCapability("WAFER_SUMMARY", "AVAILABLE", None, None),
            ),
            page=request.page,
            page_size=request.page_size,
            total=total,
            sort_by=request.sort_by.value,
            sort_direction=request.sort_direction.value,
            items=items,
            warnings=warnings,
            computed_at=datetime.now(UTC).isoformat(),
        )
