from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, Engine

from app.core.errors import DomainError
from app.domain.analytics import (
    AnalyticsBinPoint,
    AnalyticsCapability,
    AnalyticsContextRequest,
    AnalyticsCounts,
    AnalyticsDatasetContext,
    AnalyticsDatasetOverview,
    AnalyticsDetailBinEvaluation,
    AnalyticsDetailFormalSpec,
    AnalyticsDetailMeasurement,
    AnalyticsDetailMeasurementEvaluation,
    AnalyticsDetailRequest,
    AnalyticsDetailResult,
    AnalyticsDetailRow,
    AnalyticsDetailSort,
    AnalyticsDetailSourceFile,
    AnalyticsDrilldownRequest,
    AnalyticsDrilldownResult,
    AnalyticsEvaluationDrilldownContext,
    AnalyticsFilterSummary,
    AnalyticsMeasurementDrilldownContext,
    AnalyticsNormalizedFilters,
    AnalyticsOptionSet,
    AnalyticsOverviewRequest,
    AnalyticsOverviewResult,
    AnalyticsResolvedDataset,
    AnalyticsRiskItem,
    AnalyticsRuleContext,
    AnalyticsSamplingSummary,
    AnalyticsShellContextResult,
    AnalyticsSortDirection,
    AnalyticsWaferMapPoint,
    AnalyticsYieldPoint,
)
from app.infrastructure.formal_spec_context_resolver import (
    resolve_formal_spec_context,
)
from app.infrastructure.stage_run_details import run_source_identity

_CONTRACT_VERSION = "ANALYTICS_CONTEXT_V1"

_DETAIL_SORT_SQL: dict[AnalyticsDetailSort, tuple[str, ...]] = {
    AnalyticsDetailSort.UNIT_SEQUENCE: (
        "tr.run_id",
        "ISNULL(ur.unit_sequence,ur.unit_id)",
        "ur.unit_id",
    ),
    AnalyticsDetailSort.LOT: (
        "tr.lot_id",
        "COALESCE(ur.wafer_id,tr.wafer_id,N'')",
        "tr.run_id",
        "ISNULL(ur.unit_sequence,ur.unit_id)",
        "ur.unit_id",
    ),
    AnalyticsDetailSort.WAFER: (
        "COALESCE(ur.wafer_id,tr.wafer_id,N'')",
        "tr.lot_id",
        "tr.run_id",
        "ISNULL(ur.unit_sequence,ur.unit_id)",
        "ur.unit_id",
    ),
    AnalyticsDetailSort.SOURCE_ROW: (
        "ISNULL(ur.source_row_no,2147483647)",
        "tr.run_id",
        "ur.unit_id",
    ),
    AnalyticsDetailSort.RESULT: (
        "ur.overall_result",
        "tr.run_id",
        "ISNULL(ur.unit_sequence,ur.unit_id)",
        "ur.unit_id",
    ),
    AnalyticsDetailSort.SOFT_BIN: (
        "ISNULL(ur.soft_bin,N'')",
        "tr.run_id",
        "ISNULL(ur.unit_sequence,ur.unit_id)",
        "ur.unit_id",
    ),
    AnalyticsDetailSort.HARD_BIN: (
        "ISNULL(ur.hard_bin,N'')",
        "tr.run_id",
        "ISNULL(ur.unit_sequence,ur.unit_id)",
        "ur.unit_id",
    ),
}


def _detail_order_sql(request: AnalyticsDetailRequest) -> str:
    """Render ORDER BY only from validated enums; no request text reaches SQL."""

    direction = (
        "DESC" if request.sort_direction == AnalyticsSortDirection.DESC else "ASC"
    )
    return " ORDER BY " + ",".join(
        f"{column} {direction}" for column in _DETAIL_SORT_SQL[request.sort_by]
    )


def _iso_datetime(value: object) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


def _statement(sql: str, expanding: tuple[str, ...] = ()):
    statement = text(sql)
    if expanding:
        statement = statement.bindparams(
            *(bindparam(name, expanding=True) for name in expanding)
        )
    return statement


def _finite_float(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DomainError(
            "ANALYSIS_NUMERIC_CONTRACT_INVALID", f"{field} is not numeric", 409
        ) from exc
    if not math.isfinite(result):
        raise DomainError(
            "ANALYSIS_NUMERIC_CONTRACT_INVALID", f"{field} is not finite", 409
        )
    return result


def _formal_spec_from_evaluation_rows(
    rows: tuple[Mapping[str, Any], ...],
) -> AnalyticsDetailFormalSpec:
    """Select no implicit fallback: one current Released SPEC provenance or a gate."""

    spec_rows = tuple(
        row
        for row in rows
        if row["evaluation_type"] == "SPEC"
        and row["evaluation_scope_key"] == "FORMAL_SPEC"
    )
    if not spec_rows:
        return AnalyticsDetailFormalSpec(
            status="NO_SPEC",
            reason_code="FORMAL_RELEASED_SPEC_NOT_FOUND",
            evaluation_id=None,
            evaluation_result=None,
            evaluation_scope_key=None,
            spec_binding_id=None,
            spec_set_id=None,
            spec_version=None,
            spec_item_id=None,
            lsl_applied=None,
            usl_applied=None,
            lower_operator_applied=None,
            upper_operator_applied=None,
        )
    if len(spec_rows) != 1 or any(
        row["evaluation_result"] == "CONFIG_AMBIGUOUS" for row in spec_rows
    ):
        return AnalyticsDetailFormalSpec(
            status="CONFIG_AMBIGUOUS",
            reason_code="FORMAL_SPEC_CURRENT_EVALUATION_AMBIGUOUS",
            evaluation_id=None,
            evaluation_result=None,
            evaluation_scope_key=None,
            spec_binding_id=None,
            spec_set_id=None,
            spec_version=None,
            spec_item_id=None,
            lsl_applied=None,
            usl_applied=None,
            lower_operator_applied=None,
            upper_operator_applied=None,
        )
    row = spec_rows[0]
    evaluation_id = int(row["evaluation_id"])
    evaluation_result = str(row["evaluation_result"])
    evaluation_scope_key = str(row["evaluation_scope_key"])
    if evaluation_result in {"NO_MATCH", "NOT_EVALUATED"}:
        return AnalyticsDetailFormalSpec(
            status="NO_SPEC",
            reason_code="FORMAL_RELEASED_SPEC_NOT_FOUND",
            evaluation_id=evaluation_id,
            evaluation_result=evaluation_result,
            evaluation_scope_key=evaluation_scope_key,
            spec_binding_id=None,
            spec_set_id=None,
            spec_version=None,
            spec_item_id=None,
            lsl_applied=None,
            usl_applied=None,
            lower_operator_applied=None,
            upper_operator_applied=None,
        )
    spec_set_id = row["spec_set_id"]
    lsl = row["lsl_applied"]
    usl = row["usl_applied"]
    lower_operator = row["lower_operator_applied"]
    upper_operator = row["upper_operator_applied"]
    lsl_value = _finite_float(lsl, field="formal Spec LSL")
    usl_value = _finite_float(usl, field="formal Spec USL")
    binding_matches = row["spec_binding_id"] is None or (
        row["binding_spec_set_id"] == spec_set_id
    )
    operators_valid = (lsl is None or lower_operator in {">=", ">"}) and (
        usl is None or upper_operator in {"<=", "<"}
    )
    limits_valid = not (
        lsl_value is not None
        and usl_value is not None
        and (
            lsl_value > usl_value
            or (
                lsl_value == usl_value
                and (lower_operator == ">" or upper_operator == "<")
            )
        )
    )
    provenance_valid = (
        spec_set_id is not None
        and row["spec_version"] is not None
        and row["spec_set_status"] == "RELEASED"
        and row["spec_item_id"] is not None
        and row["item_spec_set_id"] == spec_set_id
        and binding_matches
        and (lsl is not None or usl is not None)
        and operators_valid
        and limits_valid
    )
    if not provenance_valid:
        return AnalyticsDetailFormalSpec(
            status="INVALID",
            reason_code="FORMAL_SPEC_PROVENANCE_INVALID",
            evaluation_id=evaluation_id,
            evaluation_result=evaluation_result,
            evaluation_scope_key=evaluation_scope_key,
            spec_binding_id=None,
            spec_set_id=None,
            spec_version=None,
            spec_item_id=None,
            lsl_applied=None,
            usl_applied=None,
            lower_operator_applied=None,
            upper_operator_applied=None,
        )
    return AnalyticsDetailFormalSpec(
        "RESOLVED",
        None,
        evaluation_id,
        evaluation_result,
        evaluation_scope_key,
        (int(row["spec_binding_id"]) if row["spec_binding_id"] is not None else None),
        int(spec_set_id),
        str(row["spec_version"]),
        int(row["spec_item_id"]),
        lsl_value,
        usl_value,
        (str(lower_operator) if lower_operator is not None else None),
        (str(upper_operator) if upper_operator is not None else None),
    )


def _condition_text(value: object) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise DomainError(
            "ANALYSIS_SPEC_CONTRACT_INVALID",
            "test-condition metadata is not valid JSON",
            409,
        ) from exc
    if not isinstance(decoded, dict) or not set(decoded).issubset(
        {"text", "bias1", "bias2"}
    ):
        raise DomainError(
            "ANALYSIS_SPEC_CONTRACT_INVALID",
            "test-condition metadata contains unsupported fields",
            409,
        )
    normalized: dict[str, str] = {}
    for key in ("text", "bias1", "bias2"):
        raw = decoded.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise DomainError(
                "ANALYSIS_SPEC_CONTRACT_INVALID",
                "test-condition metadata contains a non-text value",
                409,
            )
        compact = " ".join(raw.split())
        if compact:
            normalized[key] = compact
    if not normalized:
        return None
    if set(normalized) == {"text"}:
        return normalized["text"]
    return json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _source_identity(row: Mapping[str, Any]) -> str:
    return run_source_identity(row)


def _normalized_filters(request: AnalyticsContextRequest) -> AnalyticsNormalizedFilters:
    filters = request.filters
    return AnalyticsNormalizedFilters(
        lot_ids=tuple(sorted(filters.lot_ids)),
        wafer_ids=tuple(sorted(filters.wafer_ids)),
        bin_codes=tuple(sorted(filters.bin_codes)),
        overall_results=tuple(sorted(item.value for item in filters.overall_results)),
        source_ids=tuple(sorted(filters.source_ids)),
        tester_ids=tuple(sorted(filters.tester_ids)),
        program_versions=tuple(sorted(filters.program_versions)),
        test_conditions=tuple(sorted(filters.test_conditions)),
    )


def _hashes(request: AnalyticsContextRequest) -> AnalyticsFilterSummary:
    normalized = _normalized_filters(request)
    filters_payload = asdict(normalized)
    filter_hash = hashlib.sha256(
        json.dumps(
            filters_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    context_payload = {
        "datasets": sorted(
            (item.dataset_id, item.version_no) for item in request.datasets
        ),
        "filter_hash": filter_hash,
        "parameters": sorted(request.parameters),
    }
    context_hash = hashlib.sha256(
        json.dumps(context_payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest()
    return AnalyticsFilterSummary(
        normalized_filters=normalized,
        parameters=tuple(sorted(request.parameters)),
        filter_hash=filter_hash,
        context_hash=context_hash,
    )


def _risk_summary(
    *,
    capabilities: tuple[AnalyticsCapability, ...],
    counts: AnalyticsCounts,
    rule_context: AnalyticsRuleContext,
    evaluation_counts: Mapping[tuple[str, str, str | None, str | None], int],
) -> tuple[AnalyticsRiskItem, ...]:
    """Build factual Overview risks without inventing an unapproved statistic."""

    items: list[AnalyticsRiskItem] = []
    known_total = counts.known_yield_denominator
    unknown_total = counts.unknown_count + counts.abort_count
    if counts.fail_count:
        items.append(
            AnalyticsRiskItem(
                code="FAIL_RESULT",
                category="YIELD",
                severity="WARNING",
                status="ACTIVE",
                reason_code="ANALYSIS_FAIL_RESULT_PRESENT",
                title="存在 FAIL Unit",
                message="FAIL 占比仅以 PASS + FAIL 为分母，UNKNOWN / ABORT 不被偷偷算入。",
                affected_count=counts.fail_count,
                denominator_count=known_total,
                rate=counts.fail_count / known_total if known_total else None,
                drilldown_target="DETAIL:RESULT:FAIL",
                rule_versions=(),
            )
        )
    if unknown_total:
        items.append(
            AnalyticsRiskItem(
                code="UNKNOWN_OR_ABORT_RESULT",
                category="DATA_QUALITY",
                severity="WARNING",
                status="ACTIVE",
                reason_code="ANALYSIS_RESULT_POPULATION_INCOMPLETE",
                title="存在 UNKNOWN / ABORT",
                message="这些 Unit 不进入 Yield 分母，需通过明细追溯来源。",
                affected_count=unknown_total,
                denominator_count=counts.included_units,
                rate=(
                    unknown_total / counts.included_units
                    if counts.included_units
                    else None
                ),
                drilldown_target="DETAIL:RESULT",
                rule_versions=(),
            )
        )
    if counts.missing_measurements:
        items.append(
            AnalyticsRiskItem(
                code="MISSING_MEASUREMENT",
                category="DATA_QUALITY",
                severity="WARNING",
                status="ACTIVE",
                reason_code="ANALYSIS_MEASUREMENT_POPULATION_INCOMPLETE",
                title="存在缺失测量",
                message="缺失、未测或无值测量已计数，未被补零或静默删除。",
                affected_count=counts.missing_measurements,
                denominator_count=counts.included_units,
                rate=(
                    counts.missing_measurements / counts.included_units
                    if counts.included_units
                    else None
                ),
                drilldown_target="DETAIL:MEASUREMENT",
                rule_versions=(),
            )
        )
    if not known_total:
        items.append(
            AnalyticsRiskItem(
                code="YIELD_NOT_ASSESSABLE",
                category="CAPABILITY",
                severity="WARNING",
                status="GATED",
                reason_code="YIELD_DENOMINATOR_EMPTY",
                title="Yield 不可评价",
                message="当前 Context 没有明确 PASS / FAIL，系统保持 NULL 而不是显示 0%。",
                affected_count=counts.included_units,
                denominator_count=counts.included_units,
                rate=None,
                drilldown_target="DETAIL:RESULT",
                rule_versions=(),
            )
        )
    for capability in capabilities:
        if capability.status == "AVAILABLE" or capability.code in {"OVERVIEW", "YIELD"}:
            continue
        items.append(
            AnalyticsRiskItem(
                code=f"CAPABILITY_{capability.code}",
                category="CAPABILITY",
                severity="INFO",
                status="GATED",
                reason_code=capability.reason_code,
                title=f"{capability.code} 当前不可用",
                message=capability.message or "当前 Context 不满足该能力合同。",
                affected_count=0,
                denominator_count=counts.included_units,
                rate=None,
                drilldown_target=None,
                rule_versions=(),
            )
        )
    by_evaluation_version: dict[tuple[str, str | None, str | None], dict[str, int]] = (
        defaultdict(lambda: defaultdict(int))
    )
    for (
        evaluation_type,
        result,
        rule_code,
        version,
    ), count in evaluation_counts.items():
        by_evaluation_version[(evaluation_type, rule_code, version)][result] += count
    for (evaluation_type, rule_code, version), result_counts in sorted(
        by_evaluation_version.items(),
        key=lambda item: (item[0][0], item[0][1] or "", item[0][2] or ""),
    ):
        denominator = sum(result_counts.values())
        affected_results = tuple(
            sorted(
                result
                for result, count in result_counts.items()
                if result != "PASS" and count
            )
        )
        affected = sum(result_counts[result] for result in affected_results)
        if not affected:
            continue
        fail_count = result_counts.get("FAIL", 0)
        version_label = version or "UNVERSIONED"
        rule_label = rule_code or "UNVERSIONED_RULE"
        items.append(
            AnalyticsRiskItem(
                code=f"EVALUATION_{evaluation_type}:{rule_label}:{version_label}",
                category="EVALUATION",
                severity="CRITICAL" if fail_count else "WARNING",
                status="ACTIVE",
                reason_code=(
                    "ANALYSIS_EVALUATION_FAILED"
                    if fail_count
                    else "ANALYSIS_EVALUATION_INCOMPLETE"
                ),
                title=f"{evaluation_type} / {rule_label}@{version_label} 评价异常",
                message=(
                    f"当前筛选中 {affected} / {denominator} 条当前评价不是 PASS；"
                    "该行只对应一个 exact Rule Version，下钻只返回至少含一条该异常评价的 Unit。"
                ),
                affected_count=affected,
                denominator_count=denominator,
                rate=affected / denominator if denominator else None,
                drilldown_target="DETAIL:EVALUATION",
                rule_versions=(version,) if version is not None else (),
                aggregate_drilldown_context=AnalyticsEvaluationDrilldownContext(
                    evaluation_type=evaluation_type,
                    evaluation_results=affected_results,
                    rule_code=rule_code,
                    rule_version=version,
                ),
            )
        )
    if not rule_context.evaluation_rule_versions:
        items.append(
            AnalyticsRiskItem(
                code="STATISTICAL_RISK_NOT_EVALUATED",
                category="RULE_GATE",
                severity="INFO",
                status="GATED",
                reason_code="ANALYSIS_RULE_NOT_APPROVED",
                title="未发现持久化统计评价",
                message=(
                    "当前 Context 没有持久化 Measurement Evaluation 规则版本；"
                    "这不代表下方即时风险的执行状态。即时 Cpk/PAT/SYL/SBL/SPC/Margin "
                    "仍须由用户按已批准并激活的 exact Rule 显式执行。"
                ),
                affected_count=0,
                denominator_count=counts.included_units,
                rate=None,
                drilldown_target="QUALITY",
                rule_versions=(),
            )
        )
    else:
        items.append(
            AnalyticsRiskItem(
                code="STATISTICAL_RISK_SCOPE",
                category="RULE_GATE",
                severity="INFO",
                status="GATED",
                reason_code="ANALYSIS_RULE_SCOPE_EXPLICIT",
                title="持久化统计评价按版本解释",
                message=(
                    "基础风险表只汇总已存在的当前 Measurement Evaluation；"
                    "下方即时方法独立按 exact Rule 显式执行，不与持久化结果混算。"
                ),
                affected_count=0,
                denominator_count=counts.included_units,
                rate=None,
                drilldown_target="QUALITY",
                rule_versions=rule_context.evaluation_rule_versions,
            )
        )
    return tuple(items)


class SqlAnalyticsService:
    """Read-only unified Context service for formal Current Dataset analytics."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _context_rows(
        connection: Connection, request: AnalyticsContextRequest
    ) -> tuple[Mapping[str, Any], ...]:
        rows: list[Mapping[str, Any]] = []
        for reference in request.datasets:
            row = (
                connection.execute(
                    text(
                        "SELECT dv.dataset_version_id,dv.dataset_id,dv.version_no,dv.input_batch_id,"
                        "dv.status,dv.is_current,dv.spec_set_id,d.dataset_name,"
                        "d.test_stage,d.supplier_id,d.product_id,p.product_name,"
                        "ss.version_code AS spec_version "
                        "FROM dataset.dataset_version dv "
                        "JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                        "LEFT JOIN mdm.product p ON p.product_id=d.product_id "
                        "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=dv.spec_set_id "
                        "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    ),
                    {
                        "dataset": reference.dataset_id,
                        "version": reference.version_no,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise DomainError(
                    "DATASET_VERSION_NOT_FOUND", "所选正式数据版本不存在，请返回产品与批次重新选择", 404
                )
            if str(row["status"]) != "PUBLISHED" or not bool(row["is_current"]):
                raise DomainError(
                    "ANALYSIS_VERSION_NOT_CURRENT",
                    "所选版本已被更新或归档，请返回产品与批次选择当前正式版本",
                    409,
                )
            rows.append(row)
        stages = {str(row["test_stage"]) for row in rows}
        if len(stages) != 1 or not stages.issubset({"CP", "FT"}):
            raise DomainError(
                "ANALYSIS_STAGE_INCOMPATIBLE",
                "CP 与 FT 数据不能合并比较，请选择同一测试阶段的数据",
                409,
            )
        if len(rows) > 1 and next(iter(stages)) == "CP":
            spec_ids = {row["spec_set_id"] for row in rows}
            if None in spec_ids or len(spec_ids) != 1:
                raise DomainError(
                    "ANALYSIS_SPEC_INCOMPATIBLE",
                    "所选 CP 数据尚未证明使用同一有效规格，请选择规格一致的数据，或分别分析",
                    409,
                )
        return tuple(rows)

    @staticmethod
    def _dataset_context(
        rows: tuple[Mapping[str, Any], ...],
    ) -> AnalyticsDatasetContext:
        return AnalyticsDatasetContext(
            resolved_datasets=tuple(
                AnalyticsResolvedDataset(
                    dataset_id=int(row["dataset_id"]),
                    version_no=int(row["version_no"]),
                    dataset_name=str(row["dataset_name"]),
                    test_stage=str(row["test_stage"]),
                    product_name=(
                        str(row["product_name"])
                        if row["product_name"] is not None
                        else None
                    ),
                )
                for row in rows
            ),
            test_stage=str(rows[0]["test_stage"]),
            current_published_verified=True,
        )

    @staticmethod
    def _source_rows(
        connection: Connection, row: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            connection.execute(
                text(
                    "SELECT DISTINCT tr.run_id,tr.metadata_json,(SELECT sd.source_id FROM test.ft_run_detail sd WHERE sd.run_id=tr.run_id) AS source_id,tr.tester_id,"
                    "tr.program_version_id,"
                    "pv.version_code AS program_version "
                    "FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                    "LEFT JOIN mdm.test_program_version pv "
                    "ON pv.program_version_id=tr.program_version_id "
                    "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                ),
                {"dataset": int(row["dataset_id"]), "version": int(row["version_no"])},
            )
            .mappings()
            .all()
        )

    @staticmethod
    def _item_rows(
        connection: Connection, row: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            connection.execute(
                text(
                    "SELECT DISTINCT tid.test_item_id,tid.raw_item_name,"
                    "tid.canonical_parameter_code,tid.step_code,tid.sequence_no,"
                    "tid.unit_code,tid.program_lsl,tid.program_usl,tid.condition_json "
                    "FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset_version_run dvr "
                    "ON dvr.dataset_version_id=dv.dataset_version_id "
                    "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                    "JOIN mdm.test_item_definition tid "
                    "ON tid.program_version_id=tr.program_version_id "
                    "WHERE dv.dataset_id=:dataset AND dv.version_no=:version "
                    "AND tid.is_analysis_parameter=1 AND tid.raw_item_name IS NOT NULL"
                ),
                {"dataset": int(row["dataset_id"]), "version": int(row["version_no"])},
            )
            .mappings()
            .all()
        )

    @staticmethod
    def _filter_sql(
        request: AnalyticsContextRequest,
        *,
        source_run_ids: tuple[int, ...] | None,
        condition_item_ids: tuple[int, ...] | None,
    ) -> tuple[str, dict[str, object], tuple[str, ...]]:
        filters = request.filters
        clauses: list[str] = []
        parameters: dict[str, object] = {}
        expanding: list[str] = []
        values = (
            ("lot_ids", tuple(filters.lot_ids), "tr.lot_id IN :lot_ids"),
            (
                "wafer_ids",
                tuple(filters.wafer_ids),
                "COALESCE(ur.wafer_id,tr.wafer_id) IN :wafer_ids",
            ),
            (
                "bin_codes",
                tuple(filters.bin_codes),
                "COALESCE(ur.soft_bin,ur.hard_bin,N'UNKNOWN') IN :bin_codes",
            ),
            (
                "overall_results",
                tuple(item.value for item in filters.overall_results),
                "ur.overall_result IN :overall_results",
            ),
            ("tester_ids", tuple(filters.tester_ids), "tr.tester_id IN :tester_ids"),
            (
                "program_versions",
                tuple(filters.program_versions),
                "pv.version_code IN :program_versions",
            ),
        )
        for name, selected, clause in values:
            if selected:
                clauses.append(clause)
                parameters[name] = selected
                expanding.append(name)
        if source_run_ids is not None:
            if source_run_ids:
                clauses.append("tr.run_id IN :source_run_ids")
                parameters["source_run_ids"] = source_run_ids
                expanding.append("source_run_ids")
            else:
                clauses.append("1=0")
        if condition_item_ids is not None:
            if condition_item_ids:
                clauses.append(
                    "EXISTS(SELECT 1 FROM test.measurement condition_m "
                    "WHERE condition_m.unit_id=ur.unit_id "
                    "AND condition_m.test_item_id IN :condition_item_ids)"
                )
                parameters["condition_item_ids"] = condition_item_ids
                expanding.append("condition_item_ids")
            else:
                clauses.append("1=0")
        return (
            " AND " + " AND ".join(clauses) if clauses else "",
            parameters,
            tuple(expanding),
        )

    @staticmethod
    def _base_join() -> str:
        return (
            " FROM dataset.dataset_version dv "
            "JOIN dataset.dataset_version_run dvr "
            "ON dvr.dataset_version_id=dv.dataset_version_id "
            "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
            "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
            "LEFT JOIN mdm.test_program_version pv "
            "ON pv.program_version_id=tr.program_version_id "
        )

    @staticmethod
    def _overview_reference_cte(
        rows: tuple[Mapping[str, Any], ...],
    ) -> tuple[str, dict[str, object]]:
        """Build a SQL Server 2014 compatible, parameterized Dataset reference CTE.

        Overview accepts up to eight Current Dataset Versions.  Keeping those
        references in one CTE lets the expensive Unit/Measurement projections run
        once for the whole comparison instead of once per Dataset.
        """

        selects: list[str] = []
        parameters: dict[str, object] = {}
        for index, row in enumerate(rows):
            dataset_name = f"overview_dataset_{index}"
            version_name = f"overview_version_{index}"
            selects.append(
                f"SELECT CAST(:{dataset_name} AS bigint),CAST(:{version_name} AS int)"
            )
            parameters[dataset_name] = int(row["dataset_id"])
            parameters[version_name] = int(row["version_no"])
        return (
            "WITH requested_datasets(dataset_id,version_no) AS ("
            + " UNION ALL ".join(selects)
            + ") ",
            parameters,
        )

    @staticmethod
    def _overview_batch_join() -> str:
        return (
            " FROM requested_datasets requested "
            "JOIN dataset.dataset_version dv "
            "ON dv.dataset_id=requested.dataset_id "
            "AND dv.version_no=requested.version_no "
            "JOIN dataset.dataset_version_run dvr "
            "ON dvr.dataset_version_id=dv.dataset_version_id "
            "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
            "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
            "LEFT JOIN mdm.test_program_version pv "
            "ON pv.program_version_id=tr.program_version_id "
        )

    @staticmethod
    def _selected_run_ids(
        request: AnalyticsContextRequest, source_rows: tuple[Mapping[str, Any], ...]
    ) -> tuple[int, ...] | None:
        if not request.filters.source_ids:
            return None
        selected = set(request.filters.source_ids)
        return tuple(
            sorted(
                int(row["run_id"])
                for row in source_rows
                if _source_identity(row) in selected
            )
        )

    @staticmethod
    def _selected_condition_item_ids(
        request: AnalyticsContextRequest, item_rows: tuple[Mapping[str, Any], ...]
    ) -> tuple[int, ...] | None:
        if not request.filters.test_conditions:
            return None
        selected = set(request.filters.test_conditions)
        return tuple(
            sorted(
                int(row["test_item_id"])
                for row in item_rows
                if _condition_text(row["condition_json"]) in selected
            )
        )

    @staticmethod
    def _parameter_ids(
        item_rows: tuple[Mapping[str, Any], ...], parameters: tuple[str, ...]
    ) -> tuple[int, ...]:
        if not parameters:
            return ()
        selected = set(parameters)
        identities: dict[str, set[tuple[object, ...]]] = defaultdict(set)
        ids: dict[str, list[int]] = defaultdict(list)
        for row in item_rows:
            name = str(row["raw_item_name"])
            if name not in selected:
                continue
            identities[name].add(
                (
                    str(row["canonical_parameter_code"] or ""),
                    str(row["step_code"]),
                    int(row["sequence_no"]),
                    str(row["unit_code"] or ""),
                    _finite_float(row["program_lsl"], field=f"{name} LSL"),
                    _finite_float(row["program_usl"], field=f"{name} USL"),
                    _condition_text(row["condition_json"]),
                )
            )
            ids[name].append(int(row["test_item_id"]))
        missing = sorted(selected - set(ids))
        incompatible = sorted(
            name for name, values in identities.items() if len(values) != 1
        )
        if missing or incompatible:
            raise DomainError(
                "ANALYSIS_PARAMETER_INCOMPATIBLE",
                "所选参数不能在当前数据间直接比较。"
                + (f"缺少参数：{'、'.join(missing)}。" if missing else "")
                + (f"参数名称相同但单位、测试条件、测试项身份或测试限不同：{'、'.join(incompatible)}。" if incompatible else "")
                + "请调整参数或数据选择；可以分别查看单个数据集。",
                409,
                details=[{"missing": missing, "incompatible": incompatible}],
            )
        return tuple(sorted({item for name in parameters for item in ids[name]}))

    @staticmethod
    def _rule_context(
        connection: Connection,
        context_rows: tuple[Mapping[str, Any], ...],
        request: AnalyticsContextRequest,
        *,
        bin_mapping_versions_by_dataset: (
            dict[tuple[int, int], tuple[str, ...]] | None
        ) = None,
    ) -> AnalyticsRuleContext:
        spec_versions = resolve_formal_spec_context(
            connection, context_rows, request
        ).spec_versions
        bin_versions: set[str] = set()
        for row in context_rows:
            values = (
                connection.execute(
                    text(
                        "SELECT DISTINCT bms.bin_mapping_set_id,bms.version_code "
                        "FROM dataset.dataset_version dv "
                        "JOIN dataset.dataset_version_run dvr "
                        "ON dvr.dataset_version_id=dv.dataset_version_id "
                        "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                        "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "JOIN test.unit_bin_evaluation ube ON ube.unit_id=ur.unit_id "
                        "JOIN mdm.bin_mapping_set bms "
                        "ON bms.bin_mapping_set_id=ube.bin_mapping_set_id "
                        "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    ),
                    {
                        "dataset": int(row["dataset_id"]),
                        "version": int(row["version_no"]),
                    },
                )
                .mappings()
                .all()
            )
            dataset_bin_versions = tuple(
                sorted(
                    f"BIN:{int(item['bin_mapping_set_id'])}:{item['version_code']}"
                    for item in values
                )
            )
            if bin_mapping_versions_by_dataset is not None:
                bin_mapping_versions_by_dataset[
                    (int(row["dataset_id"]), int(row["version_no"]))
                ] = dataset_bin_versions
            bin_versions.update(dataset_bin_versions)
        applicable_rule_versions = SqlAnalyticsService._applicable_rule_versions(
            connection,
            context_rows,
            tuple(request.parameters),
        )
        return AnalyticsRuleContext(
            spec_versions=spec_versions,
            bin_mapping_versions=tuple(sorted(bin_versions)),
            evaluation_rule_versions=(),
            applicable_rule_versions=applicable_rule_versions,
        )

    @staticmethod
    def _activation_matches_context(
        activation: Mapping[str, Any],
        context: Mapping[str, Any],
        parameters: tuple[str, ...],
    ) -> bool:
        if str(activation["test_stage"]) != str(context["test_stage"]):
            return False
        for key in ("supplier_id", "product_id"):
            scoped = activation[key]
            actual = context[key]
            if scoped is not None and (actual is None or int(scoped) != int(actual)):
                return False
        pattern = activation["parameter_pattern"]
        if pattern is None:
            return True
        if not parameters:
            return False
        pattern_text = str(pattern)
        if pattern_text.endswith("*"):
            prefix = pattern_text[:-1]
            return all(parameter.startswith(prefix) for parameter in parameters)
        return all(parameter == pattern_text for parameter in parameters)

    @staticmethod
    def _applicable_rule_versions(
        connection: Connection,
        context_rows: tuple[Mapping[str, Any], ...],
        parameters: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Resolve unambiguous server-owned defaults for an exact analysis Context."""

        activations = tuple(
            connection.execute(
                text(
                    "SELECT rs.rule_code,rv.version_code,"
                    "rv.evaluation_rule_version_id,ra.test_stage,ra.supplier_id,"
                    "ra.product_id,ra.parameter_pattern "
                    "FROM evaluation.rule_set rs "
                    "JOIN evaluation.rule_version rv ON "
                    "rv.evaluation_rule_set_id=rs.evaluation_rule_set_id "
                    "JOIN evaluation.rule_activation ra ON "
                    "ra.evaluation_rule_version_id=rv.evaluation_rule_version_id "
                    "WHERE rs.active=1 AND rv.status='RELEASED' "
                    "AND rv.activation_status='ENABLED' AND ra.active=1 "
                    "AND (rv.effective_from_utc IS NULL OR rv.effective_from_utc<=SYSUTCDATETIME()) "
                    "AND (rv.effective_to_utc IS NULL OR rv.effective_to_utc>SYSUTCDATETIME()) "
                    "AND (ra.effective_from_utc IS NULL OR ra.effective_from_utc<=SYSUTCDATETIME()) "
                    "AND (ra.effective_to_utc IS NULL OR ra.effective_to_utc>SYSUTCDATETIME())"
                )
            )
            .mappings()
            .all()
        )
        grouped: dict[tuple[int, str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for activation in activations:
            grouped[
                (
                    int(activation["evaluation_rule_version_id"]),
                    str(activation["rule_code"]),
                    str(activation["version_code"]),
                )
            ].append(activation)

        resolved: list[str] = []
        for (version_id, rule_code, version_code), scopes in grouped.items():
            approval_rows = (
                connection.execute(
                    text(
                        "SELECT approval_role,decision FROM ("
                        "SELECT approval_role,decision,ROW_NUMBER() OVER("
                        "PARTITION BY approval_role ORDER BY decided_at_utc DESC,"
                        "rule_approval_id DESC) AS rn "
                        "FROM evaluation.rule_approval_record "
                        "WHERE evaluation_rule_version_id=:version_id"
                        ") latest WHERE rn=1"
                    ),
                    {"version_id": version_id},
                )
                .mappings()
                .all()
            )
            approvals = {
                str(row["approval_role"]): str(row["decision"])
                for row in approval_rows
            }
            if approvals != {
                "BUSINESS": "APPROVED",
                "TECHNICAL": "APPROVED",
                "QUALITY": "APPROVED",
            }:
                continue
            if all(
                any(
                    SqlAnalyticsService._activation_matches_context(
                        activation, context, parameters
                    )
                    for activation in scopes
                )
                for context in context_rows
            ):
                resolved.append(f"RULE:{rule_code}:{version_code}")
        return tuple(sorted(set(resolved)))

    def overview(self, request: AnalyticsOverviewRequest) -> AnalyticsOverviewResult:
        filter_summary = _hashes(request)
        computed_at = datetime.now(UTC).isoformat()
        overviews: list[AnalyticsDatasetOverview] = []
        yield_points: list[AnalyticsYieldPoint] = []
        bin_points: list[AnalyticsBinPoint] = []
        map_points: list[AnalyticsWaferMapPoint] = []
        warnings: set[str] = set()
        source_options: set[str] = set()
        tester_options: set[str] = set()
        program_options: set[str] = set()
        condition_options: set[str] = set()
        parameter_options: set[str] = set()
        lot_options: set[str] = set()
        wafer_options: set[str] = set()
        bin_options: set[str] = set()
        input_units = 0
        included_units = 0
        pass_count = 0
        fail_count = 0
        unknown_count = 0
        abort_count = 0
        missing_measurements = 0
        bin_mapping_complete = True
        evaluation_counts: dict[tuple[str, str, str | None, str | None], int] = (
            defaultdict(int)
        )
        map_capability = AnalyticsCapability(
            code="WAFER_MAP",
            status="UNAVAILABLE",
            reason_code="ANALYSIS_CAPABILITY_UNAVAILABLE",
            message="当前筛选未解析为一片具备完整唯一坐标的 CP Wafer",
        )
        with self._engine.connect() as connection:
            context_rows = self._context_rows(connection, request)
            dataset_context = self._dataset_context(context_rows)
            reference_cte, reference_parameters = self._overview_reference_cte(
                context_rows
            )
            bin_mapping_versions_by_dataset: dict[tuple[int, int], tuple[str, ...]] = {}
            rule_context = self._rule_context(
                connection,
                context_rows,
                request,
                bin_mapping_versions_by_dataset=bin_mapping_versions_by_dataset,
            )
            focus_id = request.focus_dataset_id or int(context_rows[0]["dataset_id"])

            selected_source_ids: set[int] = set()
            selected_condition_ids: set[int] = set()
            selected_parameter_ids: set[int] = set()
            evaluation_program_version_ids: set[int] = set()
            evaluation_item_scope_complete = True
            source_rows_by_dataset: dict[
                tuple[int, int], tuple[Mapping[str, Any], ...]
            ] = {}
            item_rows_by_dataset: dict[
                tuple[int, int], tuple[Mapping[str, Any], ...]
            ] = {}
            input_counts: dict[tuple[int, int], int] = {}
            base_join = self._base_join()
            for context in sorted(
                context_rows,
                key=lambda item: (int(item["dataset_id"]), int(item["version_no"])),
            ):
                dataset_id = int(context["dataset_id"])
                version_no = int(context["version_no"])
                key = (dataset_id, version_no)
                source_rows = self._source_rows(connection, context)
                item_rows = self._item_rows(connection, context)
                source_rows_by_dataset[key] = source_rows
                item_rows_by_dataset[key] = item_rows
                for source in source_rows:
                    program_version_id = source.get("program_version_id")
                    if program_version_id is None:
                        evaluation_item_scope_complete = False
                    else:
                        evaluation_program_version_ids.add(int(program_version_id))
                source_options.update(_source_identity(row) for row in source_rows)
                tester_options.update(
                    str(row["tester_id"])
                    for row in source_rows
                    if row["tester_id"] is not None and str(row["tester_id"]).strip()
                )
                program_options.update(
                    str(row["program_version"])
                    for row in source_rows
                    if row["program_version"] is not None
                    and str(row["program_version"]).strip()
                )
                for item in item_rows:
                    parameter_options.add(str(item["raw_item_name"]))
                    condition = _condition_text(item["condition_json"])
                    if condition:
                        condition_options.add(condition)
                if request.filters.source_ids:
                    selected_source_ids.update(
                        self._selected_run_ids(request, source_rows) or ()
                    )
                if request.filters.test_conditions:
                    selected_condition_ids.update(
                        self._selected_condition_item_ids(request, item_rows) or ()
                    )
                if request.parameters:
                    selected_parameter_ids.update(
                        self._parameter_ids(item_rows, tuple(request.parameters))
                    )

            filter_sql, filter_parameters, expanding = self._filter_sql(
                request,
                source_run_ids=(
                    tuple(sorted(selected_source_ids))
                    if request.filters.source_ids
                    else None
                ),
                condition_item_ids=(
                    tuple(sorted(selected_condition_ids))
                    if request.filters.test_conditions
                    else None
                ),
            )
            batch_parameters = {**reference_parameters, **filter_parameters}
            batch_join = self._overview_batch_join()
            has_unit_filters = bool(filter_sql)
            if has_unit_filters:
                for context in context_rows:
                    dataset_id = int(context["dataset_id"])
                    version_no = int(context["version_no"])
                    key = (dataset_id, version_no)
                    input_counts[key] = int(
                        connection.execute(
                            text(
                                "SELECT COUNT_BIG(*)"
                                + base_join
                                + "WHERE dv.dataset_id=:dataset "
                                "AND dv.version_no=:version"
                            ),
                            {"dataset": dataset_id, "version": version_no},
                        ).scalar_one()
                    )
                    option_rows = (
                        connection.execute(
                            text(
                                "SELECT DISTINCT tr.lot_id,"
                                "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                                "COALESCE(ur.soft_bin,ur.hard_bin,N'UNKNOWN') "
                                "AS bin_code"
                                + base_join
                                + "WHERE dv.dataset_id=:dataset "
                                "AND dv.version_no=:version"
                            ),
                            {"dataset": dataset_id, "version": version_no},
                        )
                        .mappings()
                        .all()
                    )
                    lot_options.update(str(row["lot_id"]) for row in option_rows)
                    wafer_options.update(
                        str(row["wafer_id"])
                        for row in option_rows
                        if row["wafer_id"] is not None and str(row["wafer_id"]).strip()
                    )
                    bin_options.update(str(row["bin_code"]) for row in option_rows)

            option_bin_select = (
                ""
                if has_unit_filters
                else ",COALESCE(ur.soft_bin,ur.hard_bin,N'UNKNOWN') AS option_bin_code"
            )
            option_bin_group = (
                ""
                if has_unit_filters
                else ",COALESCE(ur.soft_bin,ur.hard_bin,N'UNKNOWN')"
            )

            raw_trend_rows = tuple(
                connection.execute(
                    _statement(
                        reference_cte
                        + "SELECT requested.dataset_id,requested.version_no,"
                        "tr.run_id,tr.started_at_utc,tr.lot_id,"
                        "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                        "MIN(ur.unit_id) AS drilldown_unit_id,"
                        "COUNT_BIG(*) AS unit_count,"
                        "SUM(CASE WHEN ur.overall_result='PASS' THEN CONVERT(bigint,1) ELSE 0 END) AS pass_count,"
                        "SUM(CASE WHEN ur.overall_result='FAIL' THEN CONVERT(bigint,1) ELSE 0 END) AS fail_count,"
                        "SUM(CASE WHEN ur.overall_result='UNKNOWN' THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_count,"
                        "SUM(CASE WHEN ur.overall_result='ABORT' THEN CONVERT(bigint,1) ELSE 0 END) AS abort_count"
                        + option_bin_select
                        + batch_join
                        + "WHERE 1=1"
                        + filter_sql
                        + " GROUP BY requested.dataset_id,requested.version_no,"
                        "tr.run_id,tr.started_at_utc,tr.lot_id,"
                        "COALESCE(ur.wafer_id,tr.wafer_id)" + option_bin_group + " "
                        "ORDER BY requested.dataset_id,requested.version_no,"
                        "CASE WHEN tr.started_at_utc IS NULL THEN 1 ELSE 0 END,"
                        "tr.started_at_utc,tr.lot_id,"
                        "COALESCE(ur.wafer_id,tr.wafer_id),tr.run_id",
                        expanding,
                    ),
                    batch_parameters,
                )
                .mappings()
                .all()
            )
            if has_unit_filters:
                trend_rows: tuple[Mapping[str, Any], ...] = raw_trend_rows
            else:
                merged: dict[tuple[object, ...], dict[str, Any]] = {}
                for row in raw_trend_rows:
                    lot_options.add(str(row["lot_id"]))
                    if row["wafer_id"] is not None and str(row["wafer_id"]).strip():
                        wafer_options.add(str(row["wafer_id"]))
                    bin_options.add(str(row["option_bin_code"]))
                    trend_key = (
                        int(row["dataset_id"]),
                        int(row["version_no"]),
                        int(row["run_id"]),
                        row["started_at_utc"],
                        str(row["lot_id"]),
                        row["wafer_id"],
                    )
                    current = merged.get(trend_key)
                    if current is None:
                        current = dict(row)
                        current.pop("option_bin_code", None)
                        merged[trend_key] = current
                        continue
                    current["drilldown_unit_id"] = min(
                        int(current["drilldown_unit_id"]),
                        int(row["drilldown_unit_id"]),
                    )
                    for name in (
                        "unit_count",
                        "pass_count",
                        "fail_count",
                        "unknown_count",
                        "abort_count",
                    ):
                        current[name] = int(current[name] or 0) + int(row[name] or 0)
                trend_rows = tuple(merged.values())
            trend_by_dataset: dict[tuple[int, int], list[Mapping[str, Any]]] = (
                defaultdict(list)
            )
            counts_by_dataset: dict[tuple[int, int], dict[str, int]] = {
                (int(row["dataset_id"]), int(row["version_no"])): {
                    "unit_count": 0,
                    "pass_count": 0,
                    "fail_count": 0,
                    "unknown_count": 0,
                    "abort_count": 0,
                }
                for row in context_rows
            }
            for row in trend_rows:
                key = (int(row["dataset_id"]), int(row["version_no"]))
                trend_by_dataset[key].append(row)
                counts = counts_by_dataset[key]
                for name in counts:
                    counts[name] += int(row[name] or 0)
            if not has_unit_filters:
                input_counts = {
                    key: counts["unit_count"]
                    for key, counts in counts_by_dataset.items()
                }

            parameter_id_tuple = tuple(sorted(selected_parameter_ids))
            if parameter_id_tuple:
                missing_measurements = int(
                    connection.execute(
                        _statement(
                            reference_cte
                            + "SELECT COUNT_BIG(*)"
                            + batch_join
                            + "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                            "WHERE 1=1"
                            + filter_sql
                            + " AND m.test_item_id IN :parameter_ids "
                            "AND (m.measurement_status IN "
                            "('MISSING','NOT_TESTED','INVALID') "
                            "OR (m.value_numeric IS NULL AND m.value_text IS NULL))",
                            expanding + ("parameter_ids",),
                        ),
                        {**batch_parameters, "parameter_ids": parameter_id_tuple},
                    ).scalar_one()
                )

            evaluation_scope_item_ids: tuple[int, ...] | None = None
            if parameter_id_tuple:
                evaluation_scope_item_ids = parameter_id_tuple
            elif evaluation_item_scope_complete and evaluation_program_version_ids:
                evaluation_scope_item_ids = tuple(
                    int(row["test_item_id"])
                    for row in (
                        connection.execute(
                            _statement(
                                "SELECT DISTINCT tid.test_item_id "
                                "FROM mdm.test_item_definition tid "
                                "WHERE tid.program_version_id "
                                "IN :evaluation_program_version_ids",
                                ("evaluation_program_version_ids",),
                            ),
                            {
                                "evaluation_program_version_ids": tuple(
                                    sorted(evaluation_program_version_ids)
                                )
                            },
                        )
                        .mappings()
                        .all()
                    )
                )

            evaluation_filter = ""
            evaluation_scope_run_ids = tuple(
                sorted(
                    {
                        int(row["run_id"])
                        for rows in source_rows_by_dataset.values()
                        for row in rows
                    }
                )
            )
            evaluation_run_multiplicity: dict[int, int] = defaultdict(int)
            for rows in source_rows_by_dataset.values():
                for row in rows:
                    evaluation_run_multiplicity[int(row["run_id"])] += 1
            evaluation_expanding = expanding + ("evaluation_scope_run_ids",)
            evaluation_parameters: dict[str, object] = dict(batch_parameters)
            evaluation_parameters["evaluation_scope_run_ids"] = evaluation_scope_run_ids
            if evaluation_scope_item_ids is not None:
                evaluation_filter = " AND m.test_item_id IN :evaluation_scope_item_ids"
                evaluation_expanding += ("evaluation_scope_item_ids",)
                evaluation_parameters["evaluation_scope_item_ids"] = (
                    evaluation_scope_item_ids
                )
            current_evaluations = ()
            if evaluation_scope_run_ids:
                current_evaluations = (
                    connection.execute(
                        _statement(
                            "SELECT tr.run_id,me.evaluation_type,"
                            "me.evaluation_result,"
                            "rs.rule_code,rv.version_code,"
                            "COUNT_BIG(*) AS evaluation_count"
                            " FROM test.measurement_evaluation me "
                            "JOIN test.measurement m "
                            "ON m.measurement_id=me.measurement_id "
                            "JOIN test.unit_result ur ON ur.unit_id=m.unit_id "
                            "JOIN test.test_run tr ON tr.run_id=ur.run_id "
                            "LEFT JOIN mdm.test_program_version pv "
                            "ON pv.program_version_id=tr.program_version_id "
                            "LEFT JOIN evaluation.evaluation_run er "
                            "ON er.evaluation_run_id=me.evaluation_run_id "
                            "LEFT JOIN evaluation.rule_version rv "
                            "ON rv.evaluation_rule_version_id="
                            "er.evaluation_rule_version_id "
                            "LEFT JOIN evaluation.rule_set rs "
                            "ON rs.evaluation_rule_set_id=rv.evaluation_rule_set_id "
                            "WHERE me.is_current=1 "
                            "AND tr.run_id IN :evaluation_scope_run_ids"
                            + filter_sql
                            + evaluation_filter
                            + " GROUP BY tr.run_id,me.evaluation_type,"
                            "me.evaluation_result,"
                            "rs.rule_code,rv.version_code",
                            evaluation_expanding,
                        ),
                        evaluation_parameters,
                    )
                    .mappings()
                    .all()
                )
            for evaluation in current_evaluations:
                key = (
                    str(evaluation["evaluation_type"]),
                    str(evaluation["evaluation_result"]),
                    (
                        str(evaluation["rule_code"])
                        if evaluation["rule_code"] is not None
                        else None
                    ),
                    (
                        str(evaluation["version_code"])
                        if evaluation["version_code"] is not None
                        else None
                    ),
                )
                evaluation_counts[key] += (
                    int(evaluation["evaluation_count"])
                    * (evaluation_run_multiplicity[int(evaluation["run_id"])])
                )

            grouped_bins_by_dataset: dict[
                tuple[int, int], tuple[Mapping[str, Any], ...]
            ] = {}
            if any(bin_mapping_versions_by_dataset.values()):
                grouped_bin_rows = (
                    connection.execute(
                        _statement(
                            reference_cte
                            + "SELECT requested.dataset_id,requested.version_no,"
                            "mapped.bin_mapping_set_id,mapped.bin_definition_id,"
                            "mapped.mapping_version,mapped.bin_type,mapped.bin_code,"
                            "mapped.bin_name,mapped.failure_mode,mapped.is_pass,"
                            "COUNT_BIG(*) AS unit_count,"
                            "MIN(ur.unit_id) AS drilldown_unit_id"
                            + batch_join
                            + "OUTER APPLY(SELECT COUNT_BIG(*) AS mapping_count,"
                            "MIN(ube.bin_mapping_set_id) AS bin_mapping_set_id,"
                            "MIN(ube.bin_definition_id) AS bin_definition_id,"
                            "MIN(bms.version_code) AS mapping_version,"
                            "MIN(ube.bin_type) AS bin_type,"
                            "MIN(ube.raw_bin_code) AS bin_code,"
                            "MIN(bd.bin_name) AS bin_name,"
                            "MIN(ube.failure_mode_snapshot) AS failure_mode,"
                            "MIN(CONVERT(tinyint,ube.is_pass_snapshot)) AS is_pass "
                            "FROM test.unit_bin_evaluation ube "
                            "JOIN mdm.bin_mapping_set bms "
                            "ON bms.bin_mapping_set_id=ube.bin_mapping_set_id "
                            "JOIN mdm.bin_definition bd "
                            "ON bd.bin_definition_id=ube.bin_definition_id "
                            "WHERE ube.unit_id=ur.unit_id "
                            "AND ube.mapping_status='MATCHED' "
                            "AND ube.bin_mapping_set_id IS NOT NULL "
                            "AND ube.bin_definition_id IS NOT NULL "
                            "AND ube.is_pass_snapshot IS NOT NULL "
                            "AND ube.raw_bin_code="
                            "COALESCE(ur.soft_bin,ur.hard_bin,N'UNKNOWN') "
                            "AND ((tr.test_stage='CP' AND ube.bin_type='CP_BIN') "
                            "OR (tr.test_stage='FT' AND "
                            "((ur.soft_bin IS NOT NULL AND ube.bin_type='SOFT_BIN') "
                            "OR (ur.soft_bin IS NULL AND ur.hard_bin IS NOT NULL "
                            "AND ube.bin_type='HARD_BIN'))))) mapped "
                            + "WHERE 1=1"
                            + filter_sql
                            + " AND mapped.mapping_count=1 "
                            "GROUP BY requested.dataset_id,requested.version_no,"
                            "mapped.bin_mapping_set_id,mapped.bin_definition_id,"
                            "mapped.mapping_version,mapped.bin_type,mapped.bin_code,"
                            "mapped.bin_name,mapped.failure_mode,mapped.is_pass "
                            "ORDER BY requested.dataset_id,requested.version_no,"
                            "COUNT_BIG(*) DESC,mapped.bin_type,mapped.bin_code,"
                            "mapped.bin_mapping_set_id,mapped.bin_definition_id",
                            expanding,
                        ),
                        batch_parameters,
                    )
                    .mappings()
                    .all()
                )
                mutable_grouped_bins: dict[tuple[int, int], list[Mapping[str, Any]]] = (
                    defaultdict(list)
                )
                for row in grouped_bin_rows:
                    mutable_grouped_bins[
                        (int(row["dataset_id"]), int(row["version_no"]))
                    ].append(row)
                grouped_bins_by_dataset = {
                    key: tuple(values) for key, values in mutable_grouped_bins.items()
                }

            for context in sorted(
                context_rows,
                key=lambda item: (int(item["dataset_id"]), int(item["version_no"])),
            ):
                dataset_id = int(context["dataset_id"])
                version_no = int(context["version_no"])
                key = (dataset_id, version_no)
                counts = counts_by_dataset[key]
                if (
                    counts["pass_count"]
                    + counts["fail_count"]
                    + counts["unknown_count"]
                    + counts["abort_count"]
                    != counts["unit_count"]
                ):
                    raise DomainError(
                        "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                        "result counts do not reconcile to included units",
                        409,
                    )
                known = counts["pass_count"] + counts["fail_count"]
                overviews.append(
                    AnalyticsDatasetOverview(
                        dataset_id=dataset_id,
                        version_no=version_no,
                        known_yield_denominator=known,
                        yield_rate=counts["pass_count"] / known if known else None,
                        **counts,
                    )
                )
                input_units += input_counts.get(key, 0)
                included_units += counts["unit_count"]
                pass_count += counts["pass_count"]
                fail_count += counts["fail_count"]
                unknown_count += counts["unknown_count"]
                abort_count += counts["abort_count"]

                source_by_run = {
                    int(row["run_id"]): row for row in source_rows_by_dataset[key]
                }
                for row in trend_by_dataset.get(key, ()):
                    passed = int(row["pass_count"] or 0)
                    failed = int(row["fail_count"] or 0)
                    denominator = passed + failed
                    yield_points.append(
                        AnalyticsYieldPoint(
                            dataset_id=dataset_id,
                            version_no=version_no,
                            test_batch_id=int(context["input_batch_id"]),
                            run_id=int(row["run_id"]),
                            sequence=len(yield_points) + 1,
                            ordered_at=_iso_datetime(row["started_at_utc"]),
                            order_basis="SOURCE_STARTED_AT_UTC_THEN_RUN_ID",
                            source_id=_source_identity(
                                source_by_run[int(row["run_id"])]
                            ),
                            lot_id=str(row["lot_id"]),
                            wafer_id=(
                                str(row["wafer_id"])
                                if row["wafer_id"] is not None
                                else None
                            ),
                            unit_count=int(row["unit_count"]),
                            pass_count=passed,
                            fail_count=failed,
                            unknown_count=int(row["unknown_count"] or 0),
                            abort_count=int(row["abort_count"] or 0),
                            yield_rate=passed / denominator if denominator else None,
                            drilldown_key=f"UNIT:{int(row['drilldown_unit_id'])}",
                        )
                    )

                grouped_bins = grouped_bins_by_dataset.get(key, ())
                bin_total = sum(int(row["unit_count"]) for row in grouped_bins)
                if bin_total != counts["unit_count"]:
                    bin_mapping_complete = False
                else:
                    cumulative = 0.0
                    for row in grouped_bins:
                        percent = (
                            int(row["unit_count"]) / bin_total if bin_total else 0.0
                        )
                        cumulative += percent
                        bin_points.append(
                            AnalyticsBinPoint(
                                dataset_id=dataset_id,
                                version_no=version_no,
                                mapping_set_id=int(row["bin_mapping_set_id"]),
                                mapping_version=str(row["mapping_version"]),
                                bin_type=str(row["bin_type"]),
                                bin_code=str(row["bin_code"]),
                                bin_name=(
                                    str(row["bin_name"])
                                    if row["bin_name"] is not None
                                    else None
                                ),
                                failure_mode=(
                                    str(row["failure_mode"])
                                    if row["failure_mode"] is not None
                                    else None
                                ),
                                is_pass=bool(row["is_pass"]),
                                unit_count=int(row["unit_count"]),
                                percent=percent,
                                cumulative_percent=min(cumulative, 1.0),
                                drilldown_key=f"UNIT:{int(row['drilldown_unit_id'])}",
                            )
                        )

                if dataset_id == focus_id and str(context["test_stage"]) == "CP":
                    focus_filter_parameters = dict(filter_parameters)
                    base_parameters = {
                        "dataset": dataset_id,
                        "version": version_no,
                        **focus_filter_parameters,
                    }
                    base_join = self._base_join()
                    wafer_scope = (
                        connection.execute(
                            _statement(
                                "SELECT tr.lot_id,COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                                "COUNT_BIG(*) AS unit_count,"
                                "SUM(CASE WHEN ur.x_coord IS NULL OR ur.y_coord IS NULL "
                                "THEN CONVERT(bigint,1) ELSE 0 END) AS missing_coordinate_count"
                                + base_join
                                + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                                + filter_sql
                                + " GROUP BY tr.lot_id,COALESCE(ur.wafer_id,tr.wafer_id)",
                                expanding,
                            ),
                            base_parameters,
                        )
                        .mappings()
                        .all()
                    )
                    if (
                        len(wafer_scope) == 1
                        and int(wafer_scope[0]["missing_coordinate_count"] or 0) == 0
                    ):
                        duplicate = connection.execute(
                            _statement(
                                "SELECT TOP (1) ur.x_coord,ur.y_coord"
                                + base_join
                                + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                                + filter_sql
                                + " GROUP BY ur.x_coord,ur.y_coord HAVING COUNT_BIG(*)>1",
                                expanding,
                            ),
                            base_parameters,
                        ).first()
                        map_count = int(wafer_scope[0]["unit_count"])
                        if duplicate is None and map_count <= request.max_points:
                            points = (
                                connection.execute(
                                    _statement(
                                        "SELECT ur.unit_id,ur.x_coord,ur.y_coord,ur.soft_bin,"
                                        "ur.hard_bin,ur.overall_result"
                                        + base_join
                                        + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                                        + filter_sql
                                        + " ORDER BY ur.y_coord,ur.x_coord,ur.unit_id",
                                        expanding,
                                    ),
                                    base_parameters,
                                )
                                .mappings()
                                .all()
                            )
                            map_points.extend(
                                AnalyticsWaferMapPoint(
                                    x=int(point["x_coord"]),
                                    y=int(point["y_coord"]),
                                    bin_code=(
                                        str(point["soft_bin"] or point["hard_bin"])
                                        if point["soft_bin"] is not None
                                        or point["hard_bin"] is not None
                                        else None
                                    ),
                                    result=str(point["overall_result"]),
                                    drilldown_key=f"UNIT:{int(point['unit_id'])}",
                                )
                                for point in points
                            )
                            map_capability = AnalyticsCapability(
                                code="WAFER_MAP",
                                status="AVAILABLE",
                                reason_code=None,
                                message=None,
                            )
                        elif duplicate is not None:
                            map_capability = AnalyticsCapability(
                                code="WAFER_MAP",
                                status="UNAVAILABLE",
                                reason_code="ANALYSIS_COORDINATE_CONTRACT_INVALID",
                                message="当前 Wafer 存在重复坐标，空间图已失败关闭",
                            )
                        else:
                            map_capability = AnalyticsCapability(
                                code="WAFER_MAP",
                                status="UNAVAILABLE",
                                reason_code="ANALYSIS_RESULT_TOO_LARGE",
                                message="单 Wafer 点数超过同步地图上限",
                            )
                    elif len(wafer_scope) == 1:
                        map_capability = AnalyticsCapability(
                            code="WAFER_MAP",
                            status="UNAVAILABLE",
                            reason_code="ANALYSIS_COORDINATE_CONTRACT_INVALID",
                            message="当前 Wafer 坐标不完整，空间图已失败关闭",
                        )
            evaluation_versions = {
                f"RULE:{rule_code}:{version}"
                for _, _, rule_code, version in evaluation_counts
                if rule_code is not None and version is not None
            }
            if evaluation_versions:
                rule_context = AnalyticsRuleContext(
                    spec_versions=rule_context.spec_versions,
                    bin_mapping_versions=rule_context.bin_mapping_versions,
                    evaluation_rule_versions=tuple(sorted(evaluation_versions)),
                    applicable_rule_versions=rule_context.applicable_rule_versions,
                )
            known_total = pass_count + fail_count
            if known_total == 0:
                warnings.add("YIELD_DENOMINATOR_EMPTY")
            bin_mapping_available = (
                bool(rule_context.bin_mapping_versions) and bin_mapping_complete
            )
            if not bin_mapping_complete:
                bin_points.clear()
                warnings.add("BIN_MAPPING_INCOMPLETE_OR_AMBIGUOUS")
            if bin_points and not bin_mapping_available:
                warnings.add("BIN_MAPPING_VERSION_REQUIRED")
            if map_capability.status == "AVAILABLE" and not bin_mapping_available:
                map_capability = AnalyticsCapability(
                    code="WAFER_MAP",
                    status="UNAVAILABLE",
                    reason_code="ANALYSIS_BIN_MAPPING_REQUIRED",
                    message="当前 Dataset 未绑定版本化 Bin Mapping，Bin Map 已失败关闭",
                )
            capabilities = (
                AnalyticsCapability("OVERVIEW", "AVAILABLE", None, None),
                AnalyticsCapability(
                    "YIELD",
                    "AVAILABLE" if known_total else "UNAVAILABLE",
                    None if known_total else "YIELD_DENOMINATOR_EMPTY",
                    None if known_total else "当前范围没有明确 PASS/FAIL 分母",
                ),
                AnalyticsCapability(
                    "BIN_PARETO",
                    "AVAILABLE"
                    if bin_points and bin_mapping_available
                    else "UNAVAILABLE",
                    (
                        None
                        if bin_points and bin_mapping_available
                        else (
                            "ANALYSIS_BIN_MAPPING_REQUIRED"
                            if included_units and not bin_mapping_available
                            else "ANALYSIS_CAPABILITY_UNAVAILABLE"
                        )
                    ),
                    (
                        None
                        if bin_points and bin_mapping_available
                        else (
                            "当前 Dataset 的 Unit 未全部唯一绑定版本化 Bin Mapping，原始 Bin 不作为正式语义展示"
                            if included_units and not bin_mapping_available
                            else "当前范围没有可用 Bin"
                        )
                    ),
                ),
                map_capability,
            )
            overview_counts = AnalyticsCounts(
                input_units=input_units,
                included_units=included_units,
                excluded_units=input_units - included_units,
                pass_count=pass_count,
                fail_count=fail_count,
                unknown_count=unknown_count,
                abort_count=abort_count,
                known_yield_denominator=known_total,
                missing_measurements=missing_measurements,
                yield_rate=(pass_count / known_total if known_total else None),
                unknown_abort_denominator=included_units,
                unknown_abort_rate=(
                    (unknown_count + abort_count) / included_units
                    if included_units
                    else None
                ),
            )
        return AnalyticsOverviewResult(
            contract_version=_CONTRACT_VERSION,
            dataset_context=dataset_context,
            filter_summary=filter_summary,
            rule_context=rule_context,
            capabilities=capabilities,
            counts=overview_counts,
            sampling_summary=AnalyticsSamplingSummary(False, None, 0, 0, 0),
            options=AnalyticsOptionSet(
                lot_ids=tuple(sorted(lot_options)),
                wafer_ids=tuple(sorted(wafer_options)),
                bin_codes=tuple(sorted(bin_options)),
                source_ids=tuple(sorted(source_options)),
                tester_ids=tuple(sorted(tester_options)),
                program_versions=tuple(sorted(program_options)),
                test_conditions=tuple(sorted(condition_options)),
                parameters=tuple(sorted(parameter_options)),
            ),
            datasets=tuple(overviews),
            yield_trend=tuple(yield_points),
            bin_pareto=tuple(bin_points),
            wafer_map=tuple(map_points),
            risk_summary=_risk_summary(
                capabilities=capabilities,
                counts=overview_counts,
                rule_context=rule_context,
                evaluation_counts=evaluation_counts,
            ),
            warnings=tuple(sorted(warnings)),
            computed_at=computed_at,
        )

    def shell_context(
        self, request: AnalyticsOverviewRequest
    ) -> AnalyticsShellContextResult:
        """Resolve the shared shell without exposing Overview chart payloads.

        This path intentionally does not call ``overview``.  A disabled or expensive
        Overview group must not make Detail/Parameter/Spatial/Quality/Delivery execute
        Yield, Pareto, Wafer-map or risk queries behind the feature gate.
        """

        filter_summary = _hashes(request)
        computed_at = datetime.now(UTC).isoformat()
        warnings: set[str] = set()
        source_options: set[str] = set()
        tester_options: set[str] = set()
        program_options: set[str] = set()
        condition_options: set[str] = set()
        parameter_options: set[str] = set()
        lot_options: set[str] = set()
        wafer_options: set[str] = set()
        bin_options: set[str] = set()
        evaluation_versions: set[str] = set()
        input_units = 0
        included_units = 0
        pass_count = 0
        fail_count = 0
        unknown_count = 0
        abort_count = 0
        missing_measurements = 0

        with self._engine.connect() as connection:
            context_rows = self._context_rows(connection, request)
            dataset_context = self._dataset_context(context_rows)
            rule_context = self._rule_context(connection, context_rows, request)
            reference_cte, reference_parameters = self._overview_reference_cte(
                context_rows
            )
            batch_join = self._overview_batch_join()
            selected_source_ids: set[int] = set()
            selected_condition_ids: set[int] = set()
            selected_parameter_ids: set[int] = set()
            source_rows_by_dataset: dict[
                tuple[int, int], tuple[Mapping[str, Any], ...]
            ] = {}
            evaluation_program_version_ids: set[int] = set()
            evaluation_item_scope_complete = True
            for context in sorted(
                context_rows,
                key=lambda item: (int(item["dataset_id"]), int(item["version_no"])),
            ):
                dataset_id = int(context["dataset_id"])
                version_no = int(context["version_no"])
                source_rows = self._source_rows(connection, context)
                item_rows = self._item_rows(connection, context)
                source_rows_by_dataset[(dataset_id, version_no)] = source_rows
                for source in source_rows:
                    program_version_id = source.get("program_version_id")
                    if program_version_id is None:
                        evaluation_item_scope_complete = False
                    else:
                        evaluation_program_version_ids.add(int(program_version_id))
                source_options.update(_source_identity(row) for row in source_rows)
                tester_options.update(
                    str(row["tester_id"])
                    for row in source_rows
                    if row["tester_id"] is not None and str(row["tester_id"]).strip()
                )
                program_options.update(
                    str(row["program_version"])
                    for row in source_rows
                    if row["program_version"] is not None
                    and str(row["program_version"]).strip()
                )
                for item in item_rows:
                    parameter_options.add(str(item["raw_item_name"]))
                    condition = _condition_text(item["condition_json"])
                    if condition:
                        condition_options.add(condition)
                if request.filters.source_ids:
                    selected_source_ids.update(
                        self._selected_run_ids(request, source_rows) or ()
                    )
                if request.filters.test_conditions:
                    selected_condition_ids.update(
                        self._selected_condition_item_ids(request, item_rows) or ()
                    )
                if request.parameters:
                    selected_parameter_ids.update(
                        self._parameter_ids(item_rows, tuple(request.parameters))
                    )

            filter_sql, filter_parameters, expanding = self._filter_sql(
                request,
                source_run_ids=(
                    tuple(sorted(selected_source_ids))
                    if request.filters.source_ids
                    else None
                ),
                condition_item_ids=(
                    tuple(sorted(selected_condition_ids))
                    if request.filters.test_conditions
                    else None
                ),
            )
            batch_parameters = {**reference_parameters, **filter_parameters}
            scope_rows = (
                connection.execute(
                    text(
                        reference_cte
                        + "SELECT requested.dataset_id,requested.version_no,"
                        "tr.lot_id,COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                        "COALESCE(ur.soft_bin,ur.hard_bin,N'UNKNOWN') AS bin_code,"
                        "COUNT_BIG(*) AS unit_count,"
                        "SUM(CASE WHEN ur.overall_result='PASS' "
                        "THEN CONVERT(bigint,1) ELSE 0 END) AS pass_count,"
                        "SUM(CASE WHEN ur.overall_result='FAIL' "
                        "THEN CONVERT(bigint,1) ELSE 0 END) AS fail_count,"
                        "SUM(CASE WHEN ur.overall_result='UNKNOWN' "
                        "THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_count,"
                        "SUM(CASE WHEN ur.overall_result='ABORT' "
                        "THEN CONVERT(bigint,1) ELSE 0 END) AS abort_count"
                        + batch_join
                        + "GROUP BY requested.dataset_id,requested.version_no,"
                        "tr.lot_id,COALESCE(ur.wafer_id,tr.wafer_id),"
                        "COALESCE(ur.soft_bin,ur.hard_bin,N'UNKNOWN')"
                    ),
                    dict(reference_parameters),
                )
                .mappings()
                .all()
            )
            counts_by_dataset: dict[tuple[int, int], dict[str, int]] = {
                (int(row["dataset_id"]), int(row["version_no"])): {
                    "unit_count": 0,
                    "pass_count": 0,
                    "fail_count": 0,
                    "unknown_count": 0,
                    "abort_count": 0,
                }
                for row in context_rows
            }
            for row in scope_rows:
                scope_count = int(row["unit_count"])
                input_units += scope_count
                lot_options.add(str(row["lot_id"]))
                if row["wafer_id"] is not None and str(row["wafer_id"]).strip():
                    wafer_options.add(str(row["wafer_id"]))
                bin_options.add(str(row["bin_code"]))
                if not filter_sql:
                    counts = counts_by_dataset[
                        (int(row["dataset_id"]), int(row["version_no"]))
                    ]
                    for name in counts:
                        counts[name] += int(row[name] or 0)

            if filter_sql:
                aggregate_rows = (
                    connection.execute(
                        _statement(
                            reference_cte
                            + "SELECT requested.dataset_id,requested.version_no,"
                            "COUNT_BIG(*) AS unit_count,"
                            "SUM(CASE WHEN ur.overall_result='PASS' "
                            "THEN CONVERT(bigint,1) ELSE 0 END) AS pass_count,"
                            "SUM(CASE WHEN ur.overall_result='FAIL' "
                            "THEN CONVERT(bigint,1) ELSE 0 END) AS fail_count,"
                            "SUM(CASE WHEN ur.overall_result='UNKNOWN' "
                            "THEN CONVERT(bigint,1) ELSE 0 END) AS unknown_count,"
                            "SUM(CASE WHEN ur.overall_result='ABORT' "
                            "THEN CONVERT(bigint,1) ELSE 0 END) AS abort_count"
                            + batch_join
                            + "WHERE 1=1"
                            + filter_sql
                            + " GROUP BY requested.dataset_id,requested.version_no",
                            expanding,
                        ),
                        batch_parameters,
                    )
                    .mappings()
                    .all()
                )
                for row in aggregate_rows:
                    key = (int(row["dataset_id"]), int(row["version_no"]))
                    counts_by_dataset[key] = {
                        name: int(row[name] or 0)
                        for name in (
                            "unit_count",
                            "pass_count",
                            "fail_count",
                            "unknown_count",
                            "abort_count",
                        )
                    }

            for counts in counts_by_dataset.values():
                if (
                    counts["pass_count"]
                    + counts["fail_count"]
                    + counts["unknown_count"]
                    + counts["abort_count"]
                    != counts["unit_count"]
                ):
                    raise DomainError(
                        "ANALYSIS_AGGREGATE_CONTRACT_INVALID",
                        "result counts do not reconcile to included units",
                        409,
                    )
                included_units += counts["unit_count"]
                pass_count += counts["pass_count"]
                fail_count += counts["fail_count"]
                unknown_count += counts["unknown_count"]
                abort_count += counts["abort_count"]

            parameter_id_tuple = tuple(sorted(selected_parameter_ids))
            if parameter_id_tuple:
                missing_measurements = int(
                    connection.execute(
                        _statement(
                            reference_cte
                            + "SELECT COUNT_BIG(*)"
                            + batch_join
                            + "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                            "WHERE 1=1"
                            + filter_sql
                            + " AND m.test_item_id IN :parameter_ids "
                            "AND (m.measurement_status IN "
                            "('MISSING','NOT_TESTED','INVALID') "
                            "OR (m.value_numeric IS NULL AND m.value_text IS NULL))",
                            expanding + ("parameter_ids",),
                        ),
                        {**batch_parameters, "parameter_ids": parameter_id_tuple},
                    ).scalar_one()
                )

            evaluation_scope_item_ids: tuple[int, ...] | None = None
            if parameter_id_tuple:
                evaluation_scope_item_ids = parameter_id_tuple
            elif evaluation_item_scope_complete and evaluation_program_version_ids:
                evaluation_scope_item_ids = tuple(
                    int(row["test_item_id"])
                    for row in (
                        connection.execute(
                            _statement(
                                "SELECT DISTINCT tid.test_item_id "
                                "FROM mdm.test_item_definition tid "
                                "WHERE tid.program_version_id "
                                "IN :shell_program_version_ids",
                                ("shell_program_version_ids",),
                            ),
                            {
                                "shell_program_version_ids": tuple(
                                    sorted(evaluation_program_version_ids)
                                )
                            },
                        )
                        .mappings()
                        .all()
                    )
                )
            evaluation_scope_run_ids = tuple(
                sorted(
                    {
                        int(row["run_id"])
                        for rows in source_rows_by_dataset.values()
                        for row in rows
                    }
                )
            )
            if evaluation_scope_run_ids:
                evaluation_filter = ""
                evaluation_expanding = expanding + ("shell_run_ids",)
                evaluation_parameters: dict[str, object] = dict(batch_parameters)
                evaluation_parameters["shell_run_ids"] = evaluation_scope_run_ids
                if evaluation_scope_item_ids is not None:
                    evaluation_filter = " AND m.test_item_id IN :shell_test_item_ids"
                    evaluation_expanding += ("shell_test_item_ids",)
                    evaluation_parameters["shell_test_item_ids"] = (
                        evaluation_scope_item_ids
                    )
                current_evaluations = (
                    connection.execute(
                        _statement(
                            "SELECT DISTINCT me.evaluation_type,rv.version_code "
                            "FROM test.measurement_evaluation me "
                            "JOIN test.measurement m "
                            "ON m.measurement_id=me.measurement_id "
                            "JOIN test.unit_result ur ON ur.unit_id=m.unit_id "
                            "JOIN test.test_run tr ON tr.run_id=ur.run_id "
                            "LEFT JOIN mdm.test_program_version pv "
                            "ON pv.program_version_id=tr.program_version_id "
                            "LEFT JOIN evaluation.evaluation_run er "
                            "ON er.evaluation_run_id=me.evaluation_run_id "
                            "LEFT JOIN evaluation.rule_version rv "
                            "ON rv.evaluation_rule_version_id="
                            "er.evaluation_rule_version_id "
                            "WHERE me.is_current=1 "
                            "AND tr.run_id IN :shell_run_ids"
                            + filter_sql
                            + evaluation_filter,
                            evaluation_expanding,
                        ),
                        evaluation_parameters,
                    )
                    .mappings()
                    .all()
                )
                evaluation_versions.update(
                    f"RULE:{row['evaluation_type']}:{row['version_code']}"
                    for row in current_evaluations
                    if row["version_code"] is not None
                )

            if evaluation_versions:
                rule_context = AnalyticsRuleContext(
                    spec_versions=rule_context.spec_versions,
                    bin_mapping_versions=rule_context.bin_mapping_versions,
                    evaluation_rule_versions=tuple(sorted(evaluation_versions)),
                    applicable_rule_versions=rule_context.applicable_rule_versions,
                )

        known_total = pass_count + fail_count
        if not known_total:
            warnings.add("YIELD_DENOMINATOR_EMPTY")
        counts = AnalyticsCounts(
            input_units=input_units,
            included_units=included_units,
            excluded_units=input_units - included_units,
            pass_count=pass_count,
            fail_count=fail_count,
            unknown_count=unknown_count,
            abort_count=abort_count,
            known_yield_denominator=known_total,
            missing_measurements=missing_measurements,
            yield_rate=(pass_count / known_total if known_total else None),
            unknown_abort_denominator=included_units,
            unknown_abort_rate=(
                (unknown_count + abort_count) / included_units
                if included_units
                else None
            ),
        )
        return AnalyticsShellContextResult(
            contract_version=_CONTRACT_VERSION,
            dataset_context=dataset_context,
            filter_summary=filter_summary,
            rule_context=rule_context,
            capabilities=(
                AnalyticsCapability("CONTEXT", "AVAILABLE", None, None),
                AnalyticsCapability("DETAIL", "AVAILABLE", None, None),
                AnalyticsCapability(
                    "YIELD",
                    "AVAILABLE" if known_total else "UNAVAILABLE",
                    None if known_total else "YIELD_DENOMINATOR_EMPTY",
                    None
                    if known_total
                    else "当前筛选没有明确 PASS / FAIL，Yield 保持 NULL",
                ),
            ),
            counts=counts,
            sampling_summary=AnalyticsSamplingSummary(False, None, 0, 0, 0),
            options=AnalyticsOptionSet(
                lot_ids=tuple(sorted(lot_options)),
                wafer_ids=tuple(sorted(wafer_options)),
                bin_codes=tuple(sorted(bin_options)),
                source_ids=tuple(sorted(source_options)),
                tester_ids=tuple(sorted(tester_options)),
                program_versions=tuple(sorted(program_options)),
                test_conditions=tuple(sorted(condition_options)),
                parameters=tuple(sorted(parameter_options)),
            ),
            warnings=tuple(sorted(warnings)),
            computed_at=computed_at,
        )

    def _detail_items(
        self,
        connection: Connection,
        request: AnalyticsDetailRequest,
        context: Mapping[str, Any],
        *,
        forced_unit_id: int | None = None,
    ) -> tuple[int, tuple[AnalyticsDetailRow, ...]]:
        source_rows = self._source_rows(connection, context)
        item_rows = self._item_rows(connection, context)
        source_run_ids = self._selected_run_ids(request, source_rows)
        condition_item_ids = self._selected_condition_item_ids(request, item_rows)
        filter_sql, filter_parameters, expanding = self._filter_sql(
            request,
            source_run_ids=source_run_ids,
            condition_item_ids=condition_item_ids,
        )
        if request.evaluation_filter is not None:
            evaluation_filter = request.evaluation_filter
            filter_sql += (
                " AND EXISTS(SELECT 1 FROM test.measurement risk_m "
                "JOIN test.measurement_evaluation risk_me "
                "ON risk_me.measurement_id=risk_m.measurement_id "
                "AND risk_me.is_current=1 "
                "LEFT JOIN evaluation.evaluation_run risk_er "
                "ON risk_er.evaluation_run_id=risk_me.evaluation_run_id "
                "LEFT JOIN evaluation.rule_version risk_rv "
                "ON risk_rv.evaluation_rule_version_id="
                "risk_er.evaluation_rule_version_id "
                "LEFT JOIN evaluation.rule_set risk_rs "
                "ON risk_rs.evaluation_rule_set_id=risk_rv.evaluation_rule_set_id "
                "WHERE risk_m.unit_id=ur.unit_id "
                "AND risk_me.evaluation_type=:detail_evaluation_type "
                "AND risk_me.evaluation_result IN :detail_evaluation_results"
            )
            filter_parameters["detail_evaluation_type"] = (
                evaluation_filter.evaluation_type
            )
            filter_parameters["detail_evaluation_results"] = tuple(
                evaluation_filter.evaluation_results
            )
            expanding += ("detail_evaluation_results",)
            if evaluation_filter.rule_code is None:
                filter_sql += " AND risk_rs.rule_code IS NULL"
            else:
                filter_sql += " AND risk_rs.rule_code=:detail_rule_code"
                filter_parameters["detail_rule_code"] = evaluation_filter.rule_code
            if evaluation_filter.rule_version is None:
                filter_sql += " AND risk_rv.version_code IS NULL"
            else:
                filter_sql += " AND risk_rv.version_code=:detail_rule_version"
                filter_parameters["detail_rule_version"] = (
                    evaluation_filter.rule_version
                )
            risk_parameter_ids = self._parameter_ids(
                item_rows, tuple(request.parameters)
            )
            if risk_parameter_ids:
                filter_sql += " AND risk_m.test_item_id IN :detail_parameter_ids"
                filter_parameters["detail_parameter_ids"] = risk_parameter_ids
                expanding += ("detail_parameter_ids",)
            filter_sql += ")"
        if request.measurement_filter is not None:
            measurement_filter = request.measurement_filter
            measurement_parameter_ids = self._parameter_ids(
                item_rows, (measurement_filter.parameter,)
            )
            filter_sql += (
                " AND EXISTS(SELECT 1 FROM test.measurement aggregate_m "
                "WHERE aggregate_m.unit_id=ur.unit_id "
                "AND aggregate_m.test_item_id IN :aggregate_parameter_ids "
                "AND aggregate_m.measurement_status='MEASURED' "
                "AND aggregate_m.value_numeric IS NOT NULL"
            )
            filter_parameters["aggregate_parameter_ids"] = measurement_parameter_ids
            expanding += ("aggregate_parameter_ids",)
            if measurement_filter.lower_bound is not None:
                filter_sql += (
                    " AND aggregate_m.value_numeric>=:aggregate_lower_bound"
                    if measurement_filter.lower_inclusive
                    else " AND aggregate_m.value_numeric>:aggregate_lower_bound"
                )
                filter_parameters["aggregate_lower_bound"] = (
                    measurement_filter.lower_bound
                )
            if measurement_filter.upper_bound is not None:
                filter_sql += (
                    " AND aggregate_m.value_numeric<=:aggregate_upper_bound"
                    if measurement_filter.upper_inclusive
                    else " AND aggregate_m.value_numeric<:aggregate_upper_bound"
                )
                filter_parameters["aggregate_upper_bound"] = (
                    measurement_filter.upper_bound
                )
            filter_sql += ")"
        if forced_unit_id is not None:
            filter_sql += " AND ur.unit_id=:forced_unit_id"
            filter_parameters["forced_unit_id"] = forced_unit_id
        base_parameters = {
            "dataset": int(context["dataset_id"]),
            "version": int(context["version_no"]),
            **filter_parameters,
        }
        base_join = self._base_join()
        total = int(
            connection.execute(
                _statement(
                    "SELECT COUNT_BIG(*)"
                    + base_join
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + filter_sql,
                    expanding,
                ),
                base_parameters,
            ).scalar_one()
        )
        if forced_unit_id is None:
            if request.page == 1:
                page_select_sql = f"TOP ({int(request.page_size)}) "
                paging_sql = ""
            else:
                page_select_sql = ""
                base_parameters.update(
                    {
                        "offset": (request.page - 1) * request.page_size,
                        "page_size": request.page_size,
                    }
                )
                paging_sql = " OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY"
        else:
            page_select_sql = ""
            paging_sql = ""
        unit_rows = tuple(
            connection.execute(
                _statement(
                    "SELECT "
                    + page_select_sql
                    + "ur.unit_id,ur.logical_unit_key,tr.lot_id,"
                    "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                    "ur.x_coord,ur.y_coord,ur.soft_bin,ur.hard_bin,"
                    "ur.overall_result,ur.source_row_no,tr.run_id,tr.metadata_json,(SELECT sd.source_id FROM test.ft_run_detail sd WHERE sd.run_id=tr.run_id) AS source_id,"
                    "tr.tester_id,pv.version_code AS program_version,"
                    "cr.cleaner_code,cr.cleaner_version,pr.processing_run_id,"
                    "pr.source_file_id,sf.sha256 "
                    + base_join
                    + "JOIN ingestion.processing_run pr "
                    "ON pr.processing_run_id=tr.processing_run_id "
                    "JOIN ingestion.source_file sf "
                    "ON sf.source_file_id=pr.source_file_id "
                    "JOIN ingestion.processing_job pj ON pj.job_id=pr.job_id "
                    "LEFT JOIN ingestion.cleaner_release cr "
                    "ON cr.cleaner_release_id=pj.cleaner_release_id "
                    "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + filter_sql
                    + _detail_order_sql(request)
                    + paging_sql,
                    expanding,
                ),
                base_parameters,
            )
            .mappings()
            .all()
        )
        if not unit_rows:
            return total, ()
        unit_ids = tuple(int(row["unit_id"]) for row in unit_rows)
        processing_run_ids = tuple(
            sorted({int(row["processing_run_id"]) for row in unit_rows})
        )
        source_files: dict[int, list[AnalyticsDetailSourceFile]] = defaultdict(list)
        primary_lineage_rows = tuple(
            connection.execute(
                _statement(
                    "/* ANALYTICS_DETAIL_SOURCE_LINEAGE_PRIMARY */ "
                    "SELECT rif.processing_run_id,sf.source_file_id,r.receipt_id,"
                    "r.original_file_name,sf.sha256,ibf.ordinal_no,ibf.file_role,"
                    "rif.lineage_basis "
                    "FROM ingestion.processing_run_input_file rif "
                    "JOIN ingestion.import_batch_file ibf "
                    "ON ibf.import_batch_file_id=rif.import_batch_file_id "
                    "JOIN ingestion.source_file_receipt r ON r.receipt_id=ibf.receipt_id "
                    "JOIN ingestion.source_file sf ON sf.source_file_id=r.source_file_id "
                    "WHERE rif.processing_run_id IN :processing_run_ids "
                    "ORDER BY processing_run_id,ordinal_no,source_file_id,receipt_id",
                    ("processing_run_ids",),
                ),
                {"processing_run_ids": processing_run_ids},
            )
            .mappings()
            .all()
        )
        primary_run_ids = {
            int(row["processing_run_id"]) for row in primary_lineage_rows
        }
        fallback_run_ids = tuple(
            run_id for run_id in processing_run_ids if run_id not in primary_run_ids
        )
        fallback_lineage_rows: tuple[Mapping[str, Any], ...] = ()
        if fallback_run_ids:
            fallback_lineage_rows = tuple(
                connection.execute(
                    _statement(
                        "/* ANALYTICS_DETAIL_SOURCE_LINEAGE_FALLBACK */ "
                        "SELECT pr.processing_run_id,sf.source_file_id,r.receipt_id,"
                        "r.original_file_name,sf.sha256,ibf.ordinal_no,ibf.file_role,"
                        "CAST('PROCESSING_RUN_SOURCE' AS varchar(32)) "
                        "AS lineage_basis "
                        "FROM ingestion.processing_run pr "
                        "JOIN ingestion.processing_job pj ON pj.job_id=pr.job_id "
                        "JOIN ingestion.source_file sf "
                        "ON sf.source_file_id=pr.source_file_id "
                        "LEFT JOIN ingestion.source_file_receipt r "
                        "ON r.source_file_id=sf.source_file_id "
                        "AND (pj.import_batch_id IS NULL "
                        "OR r.import_batch_id=pj.import_batch_id) "
                        "LEFT JOIN ingestion.import_batch_file ibf "
                        "ON ibf.receipt_id=r.receipt_id "
                        "WHERE pr.processing_run_id IN :fallback_run_ids "
                        "AND NOT EXISTS(SELECT 1 "
                        "FROM ingestion.processing_run_input_file rif "
                        "WHERE rif.processing_run_id=pr.processing_run_id) "
                        "ORDER BY processing_run_id,ordinal_no,"
                        "source_file_id,receipt_id",
                        ("fallback_run_ids",),
                    ),
                    {"fallback_run_ids": fallback_run_ids},
                )
                .mappings()
                .all()
            )
        source_lineage_rows = primary_lineage_rows + fallback_lineage_rows
        for row in source_lineage_rows:
            source_files[int(row["processing_run_id"])].append(
                AnalyticsDetailSourceFile(
                    source_file_id=int(row["source_file_id"]),
                    receipt_id=(
                        int(row["receipt_id"])
                        if row["receipt_id"] is not None
                        else None
                    ),
                    original_file_name=(
                        str(row["original_file_name"])
                        if row["original_file_name"] is not None
                        else None
                    ),
                    sha256=(str(row["sha256"]) if row["sha256"] is not None else None),
                    ordinal_no=(
                        int(row["ordinal_no"])
                        if row["ordinal_no"] is not None
                        else None
                    ),
                    file_role=(
                        str(row["file_role"]) if row["file_role"] is not None else None
                    ),
                    lineage_basis=str(row["lineage_basis"]),
                )
            )
        bin_evaluations: dict[int, list[AnalyticsDetailBinEvaluation]] = defaultdict(
            list
        )
        bin_rows = (
            connection.execute(
                _statement(
                    "/* ANALYTICS_DETAIL_BIN_EVALUATIONS */ "
                    "SELECT ube.unit_bin_evaluation_id,ube.unit_id,ube.bin_type,"
                    "ube.raw_bin_code,ube.mapping_status,ube.bin_mapping_set_id,"
                    "bms.version_code AS mapping_version,ube.bin_definition_id,"
                    "bd.bin_name AS mapped_bin_name,ube.failure_mode_snapshot,"
                    "ube.is_pass_snapshot,ube.processing_run_id,ube.evaluated_at_utc "
                    "FROM test.unit_bin_evaluation ube "
                    "LEFT JOIN mdm.bin_mapping_set bms "
                    "ON bms.bin_mapping_set_id=ube.bin_mapping_set_id "
                    "LEFT JOIN mdm.bin_definition bd "
                    "ON bd.bin_definition_id=ube.bin_definition_id "
                    "WHERE ube.unit_id IN :unit_ids "
                    "ORDER BY ube.unit_id,ube.bin_type,ube.evaluated_at_utc,"
                    "ube.unit_bin_evaluation_id",
                    ("unit_ids",),
                ),
                {"unit_ids": unit_ids},
            )
            .mappings()
            .all()
        )
        for row in bin_rows:
            bin_evaluations[int(row["unit_id"])].append(
                AnalyticsDetailBinEvaluation(
                    unit_bin_evaluation_id=int(row["unit_bin_evaluation_id"]),
                    bin_type=str(row["bin_type"]),
                    raw_bin_code=str(row["raw_bin_code"]),
                    mapping_status=str(row["mapping_status"]),
                    bin_mapping_set_id=(
                        int(row["bin_mapping_set_id"])
                        if row["bin_mapping_set_id"] is not None
                        else None
                    ),
                    mapping_version=(
                        str(row["mapping_version"])
                        if row["mapping_version"] is not None
                        else None
                    ),
                    bin_definition_id=(
                        int(row["bin_definition_id"])
                        if row["bin_definition_id"] is not None
                        else None
                    ),
                    mapped_bin_name=(
                        str(row["mapped_bin_name"])
                        if row["mapped_bin_name"] is not None
                        else None
                    ),
                    failure_mode_snapshot=(
                        str(row["failure_mode_snapshot"])
                        if row["failure_mode_snapshot"] is not None
                        else None
                    ),
                    is_pass_snapshot=(
                        bool(row["is_pass_snapshot"])
                        if row["is_pass_snapshot"] is not None
                        else None
                    ),
                    processing_run_id=(
                        int(row["processing_run_id"])
                        if row["processing_run_id"] is not None
                        else None
                    ),
                    evaluated_at_utc=_iso_datetime(row["evaluated_at_utc"]) or "",
                )
            )
        parameter_ids = self._parameter_ids(item_rows, tuple(request.parameters))
        measurements: dict[int, list[AnalyticsDetailMeasurement]] = defaultdict(list)
        if parameter_ids:
            measurement_rows = (
                connection.execute(
                    _statement(
                        "/* ANALYTICS_DETAIL_MEASUREMENTS */ "
                        "SELECT m.measurement_id,m.unit_id,tid.raw_item_name,"
                        "tid.canonical_parameter_code,tid.step_code,tid.sequence_no,"
                        "m.value_numeric,m.value_text,m.measurement_status,tid.unit_code,"
                        "tid.program_lsl,tid.program_usl "
                        "FROM test.measurement m "
                        "JOIN mdm.test_item_definition tid "
                        "ON tid.test_item_id=m.test_item_id "
                        "WHERE m.unit_id IN :unit_ids "
                        "AND m.test_item_id IN :parameter_ids "
                        "ORDER BY m.unit_id,tid.sequence_no,m.measurement_id",
                        ("unit_ids", "parameter_ids"),
                    ),
                    {"unit_ids": unit_ids, "parameter_ids": parameter_ids},
                )
                .mappings()
                .all()
            )
            measurement_ids = tuple(
                int(row["measurement_id"]) for row in measurement_rows
            )
            evaluation_rows_by_measurement: dict[int, list[Mapping[str, Any]]] = (
                defaultdict(list)
            )
            if measurement_ids:
                evaluation_rows = (
                    connection.execute(
                        _statement(
                            "/* ANALYTICS_DETAIL_MEASUREMENT_EVALUATIONS */ "
                            "SELECT me.evaluation_id,me.measurement_id,"
                            "me.evaluation_type,me.evaluation_scope_key,"
                            "me.evaluation_result,me.evaluation_reason,"
                            "me.evaluation_run_id,rs.rule_code,"
                            "rv.evaluation_rule_version_id AS rule_version_id,"
                            "rv.version_code AS rule_version,me.spec_binding_id,"
                            "sb.spec_set_id AS binding_spec_set_id,ss.spec_set_id,"
                            "ss.version_code AS spec_version,ss.status AS spec_set_status,"
                            "me.spec_item_id,si.spec_set_id AS item_spec_set_id,"
                            "me.lsl_applied,me.usl_applied,"
                            "me.lower_operator_applied,me.upper_operator_applied,"
                            "me.processing_run_id,me.evaluated_at_utc "
                            "FROM test.measurement_evaluation me "
                            "LEFT JOIN evaluation.evaluation_run er "
                            "ON er.evaluation_run_id=me.evaluation_run_id "
                            "LEFT JOIN evaluation.rule_version rv "
                            "ON rv.evaluation_rule_version_id="
                            "er.evaluation_rule_version_id "
                            "LEFT JOIN evaluation.rule_set rs "
                            "ON rs.evaluation_rule_set_id=rv.evaluation_rule_set_id "
                            "LEFT JOIN mdm.spec_item si "
                            "ON si.spec_item_id=me.spec_item_id "
                            "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=si.spec_set_id "
                            "LEFT JOIN mdm.spec_binding sb "
                            "ON sb.spec_binding_id=me.spec_binding_id "
                            "WHERE me.measurement_id IN :measurement_ids "
                            "AND me.is_current=1 "
                            "ORDER BY me.measurement_id,me.evaluation_type,"
                            "me.evaluation_scope_key,me.evaluation_id",
                            ("measurement_ids",),
                        ),
                        {"measurement_ids": measurement_ids},
                    )
                    .mappings()
                    .all()
                )
                for evaluation_row in evaluation_rows:
                    evaluation_rows_by_measurement[
                        int(evaluation_row["measurement_id"])
                    ].append(evaluation_row)
            for row in measurement_rows:
                measurement_id = int(row["measurement_id"])
                current_rows = tuple(
                    evaluation_rows_by_measurement.get(measurement_id, ())
                )
                evaluations = tuple(
                    AnalyticsDetailMeasurementEvaluation(
                        evaluation_id=int(evaluation["evaluation_id"]),
                        evaluation_type=str(evaluation["evaluation_type"]),
                        evaluation_scope_key=str(evaluation["evaluation_scope_key"]),
                        evaluation_result=str(evaluation["evaluation_result"]),
                        evaluation_reason=(
                            str(evaluation["evaluation_reason"])
                            if evaluation["evaluation_reason"] is not None
                            else None
                        ),
                        evaluation_run_id=(
                            int(evaluation["evaluation_run_id"])
                            if evaluation["evaluation_run_id"] is not None
                            else None
                        ),
                        rule_code=(
                            str(evaluation["rule_code"])
                            if evaluation["rule_code"] is not None
                            else None
                        ),
                        rule_version_id=(
                            int(evaluation["rule_version_id"])
                            if evaluation["rule_version_id"] is not None
                            else None
                        ),
                        rule_version=(
                            str(evaluation["rule_version"])
                            if evaluation["rule_version"] is not None
                            else None
                        ),
                        spec_binding_id=(
                            int(evaluation["spec_binding_id"])
                            if evaluation["spec_binding_id"] is not None
                            else None
                        ),
                        spec_set_id=(
                            int(evaluation["spec_set_id"])
                            if evaluation["spec_set_id"] is not None
                            else None
                        ),
                        spec_version=(
                            str(evaluation["spec_version"])
                            if evaluation["spec_version"] is not None
                            else None
                        ),
                        spec_item_id=(
                            int(evaluation["spec_item_id"])
                            if evaluation["spec_item_id"] is not None
                            else None
                        ),
                        lsl_applied=_finite_float(
                            evaluation["lsl_applied"], field="applied LSL"
                        ),
                        usl_applied=_finite_float(
                            evaluation["usl_applied"], field="applied USL"
                        ),
                        lower_operator_applied=(
                            str(evaluation["lower_operator_applied"])
                            if evaluation["lower_operator_applied"] is not None
                            else None
                        ),
                        upper_operator_applied=(
                            str(evaluation["upper_operator_applied"])
                            if evaluation["upper_operator_applied"] is not None
                            else None
                        ),
                        processing_run_id=(
                            int(evaluation["processing_run_id"])
                            if evaluation["processing_run_id"] is not None
                            else None
                        ),
                        evaluated_at_utc=(
                            _iso_datetime(evaluation["evaluated_at_utc"]) or ""
                        ),
                    )
                    for evaluation in current_rows
                )
                measurements[int(row["unit_id"])].append(
                    AnalyticsDetailMeasurement(
                        measurement_id=measurement_id,
                        parameter=str(row["raw_item_name"]),
                        canonical_parameter_code=(
                            str(row["canonical_parameter_code"])
                            if row["canonical_parameter_code"] is not None
                            else None
                        ),
                        step_code=str(row["step_code"]),
                        sequence_no=int(row["sequence_no"]),
                        value_numeric=_finite_float(
                            row["value_numeric"], field="measurement"
                        ),
                        value_text=(
                            str(row["value_text"])
                            if row["value_text"] is not None
                            else None
                        ),
                        status=str(row["measurement_status"]),
                        unit=(
                            str(row["unit_code"])
                            if row["unit_code"] is not None
                            else None
                        ),
                        program_lsl=_finite_float(
                            row["program_lsl"], field="program LSL"
                        ),
                        program_usl=_finite_float(
                            row["program_usl"], field="program USL"
                        ),
                        program_limit_source="TEST_PROGRAM_CONFIGURATION_NOT_FORMAL_SPEC",
                        formal_spec=_formal_spec_from_evaluation_rows(current_rows),
                        evaluations=evaluations,
                    )
                )

        def detail_row(row: Mapping[str, Any]) -> AnalyticsDetailRow:
            processing_run_id = int(row["processing_run_id"])
            source_file_id = int(row["source_file_id"])
            lineage = tuple(source_files.get(processing_run_id, ()))
            if not lineage:
                lineage = (
                    AnalyticsDetailSourceFile(
                        source_file_id=source_file_id,
                        receipt_id=None,
                        original_file_name=None,
                        sha256=(
                            str(row["sha256"]) if row["sha256"] is not None else None
                        ),
                        ordinal_no=None,
                        file_role=None,
                        lineage_basis="PROCESSING_RUN_SOURCE",
                    ),
                )
            primary = next(
                (
                    item
                    for item in lineage
                    if item.source_file_id == source_file_id
                    and item.receipt_id is not None
                ),
                None,
            )
            return AnalyticsDetailRow(
                drilldown_key=f"UNIT:{int(row['unit_id'])}",
                unit_id=int(row["unit_id"]),
                logical_unit_key=str(row["logical_unit_key"]),
                lot_id=str(row["lot_id"]),
                wafer_id=(
                    str(row["wafer_id"]) if row["wafer_id"] is not None else None
                ),
                x=int(row["x_coord"]) if row["x_coord"] is not None else None,
                y=int(row["y_coord"]) if row["y_coord"] is not None else None,
                soft_bin=(
                    str(row["soft_bin"]) if row["soft_bin"] is not None else None
                ),
                hard_bin=(
                    str(row["hard_bin"]) if row["hard_bin"] is not None else None
                ),
                overall_result=str(row["overall_result"]),
                source_row_no=(
                    int(row["source_row_no"])
                    if row["source_row_no"] is not None
                    else None
                ),
                processing_run_id=processing_run_id,
                source_file_id=source_file_id,
                receipt_id=primary.receipt_id if primary is not None else None,
                original_file_name=(
                    primary.original_file_name if primary is not None else None
                ),
                sha256=(str(row["sha256"]) if row["sha256"] is not None else None),
                source_id=_source_identity(row),
                tester_id=(
                    str(row["tester_id"]) if row["tester_id"] is not None else None
                ),
                program_version=(
                    str(row["program_version"])
                    if row["program_version"] is not None
                    else None
                ),
                cleaner_release=(
                    f"{row['cleaner_code']}:{row['cleaner_version']}"
                    if row["cleaner_code"] is not None
                    and row["cleaner_version"] is not None
                    else None
                ),
                source_files=lineage,
                bin_evaluations=tuple(bin_evaluations.get(int(row["unit_id"]), ())),
                measurements=tuple(measurements.get(int(row["unit_id"]), ())),
            )

        return total, tuple(detail_row(row) for row in unit_rows)

    def detail(self, request: AnalyticsDetailRequest) -> AnalyticsDetailResult:
        overview_request = AnalyticsOverviewRequest.model_validate(
            {
                "datasets": [item.model_dump() for item in request.datasets],
                "filters": request.filters.model_dump(),
                "parameters": request.parameters,
                "focus_dataset_id": request.focus_dataset_id,
                "max_points": 100,
            }
        )
        envelope = self.shell_context(overview_request)
        with self._engine.connect() as connection:
            context_rows = self._context_rows(connection, request)
            focus = next(
                row
                for row in context_rows
                if int(row["dataset_id"]) == request.focus_dataset_id
            )
            total, items = self._detail_items(connection, request, focus)
        return AnalyticsDetailResult(
            contract_version=_CONTRACT_VERSION,
            dataset_context=envelope.dataset_context,
            filter_summary=envelope.filter_summary,
            rule_context=envelope.rule_context,
            capabilities=(AnalyticsCapability("DETAIL", "AVAILABLE", None, None),),
            counts=envelope.counts,
            sampling_summary=AnalyticsSamplingSummary(False, None, 0, 0, 0),
            evaluation_filter=(
                AnalyticsEvaluationDrilldownContext(
                    evaluation_type=request.evaluation_filter.evaluation_type,
                    evaluation_results=tuple(
                        request.evaluation_filter.evaluation_results
                    ),
                    rule_code=request.evaluation_filter.rule_code,
                    rule_version=request.evaluation_filter.rule_version,
                )
                if request.evaluation_filter is not None
                else None
            ),
            measurement_filter=(
                AnalyticsMeasurementDrilldownContext(
                    parameter=request.measurement_filter.parameter,
                    lower_bound=request.measurement_filter.lower_bound,
                    upper_bound=request.measurement_filter.upper_bound,
                    lower_inclusive=request.measurement_filter.lower_inclusive,
                    upper_inclusive=request.measurement_filter.upper_inclusive,
                )
                if request.measurement_filter is not None
                else None
            ),
            page=request.page,
            page_size=request.page_size,
            total=total,
            view=request.view.value,
            sort_by=request.sort_by.value,
            sort_direction=request.sort_direction.value,
            items=items,
            warnings=envelope.warnings,
            computed_at=datetime.now(UTC).isoformat(),
        )

    def drilldown(self, request: AnalyticsDrilldownRequest) -> AnalyticsDrilldownResult:
        unit_id = int(request.drilldown_key.split(":", 1)[1])
        detail_request_base = {
            "datasets": [item.model_dump() for item in request.datasets],
            "filters": request.filters.model_dump(),
            "parameters": request.parameters,
            "page": 1,
            "page_size": 1,
            "view": "WIDE",
            "sort_by": "UNIT_SEQUENCE",
            "sort_direction": "ASC",
        }
        with self._engine.connect() as connection:
            context_rows = self._context_rows(connection, request)
            matched: AnalyticsDetailRow | None = None
            for context in context_rows:
                detail_request = AnalyticsDetailRequest.model_validate(
                    {
                        **detail_request_base,
                        "focus_dataset_id": int(context["dataset_id"]),
                    }
                )
                _, items = self._detail_items(
                    connection,
                    detail_request,
                    context,
                    forced_unit_id=unit_id,
                )
                if items:
                    if matched is not None:
                        raise DomainError(
                            "ANALYSIS_DRILLDOWN_IDENTITY_AMBIGUOUS",
                            "drilldown identity resolved in more than one dataset",
                            409,
                        )
                    matched = items[0]
            if matched is None:
                raise DomainError(
                    "ANALYSIS_DRILLDOWN_NOT_FOUND",
                    "drilldown identity is outside the selected analytics context",
                    404,
                )
            dataset_context = self._dataset_context(context_rows)
            rule_context = self._rule_context(connection, context_rows, request)
        return AnalyticsDrilldownResult(
            contract_version=_CONTRACT_VERSION,
            dataset_context=dataset_context,
            filter_summary=_hashes(request),
            rule_context=rule_context,
            unit=matched,
            warnings=(),
            computed_at=datetime.now(UTC).isoformat(),
        )
