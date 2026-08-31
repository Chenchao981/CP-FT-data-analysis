from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, Engine

from app.core.errors import DomainError
from app.domain.analysis_rules import (
    AnalysisRuleParameters,
    LimitRoundingPolicy,
    MissingValuePolicy,
    OutlierPolicy,
    RetestPolicy,
    SigmaDefinition,
    SpcRunRuleMode,
)
from app.domain.analytics import (
    AnalyticsCapability,
    AnalyticsRuleContext,
    AnalyticsSamplingSummary,
)
from app.domain.formal_pat_contract import (
    FORMAL_PAT_ADAPTER_CONTRACT_VERSION,
    FORMAL_PAT_ADAPTER_MANIFEST_SHA256,
    FORMAL_PAT_ALGORITHM_CODE,
    FORMAL_PAT_SOURCE_SHA256,
)
from app.domain.quality_evaluation import (
    BinCooccurrenceCell,
    MarginGroupResult,
    MarginPoint,
    PassFailDistributionGroup,
    PassFailHistogramBin,
    PatGroupResult,
    QualityCalculationCounts,
    QualityEvaluationRequest,
    QualityEvaluationResult,
    QualityEvidencePoint,
    QualityGroupBy,
    QualityParameterIdentity,
    QualityRuleProvenance,
    SblBinLimit,
    SblGroupRate,
    SpcGroupResult,
    SpcPoint,
    SylDatasetLimit,
    SylGroupYield,
)
from app.infrastructure.formal_pat_adapter import calculate_formal_pat
from app.infrastructure.formal_spec_resolver import resolve_released_formal_spec
from app.infrastructure.quality_evaluation_kernels import (
    OrderedKernelValue,
    bin_cooccurrence,
    margin_oos,
    pass_fail_distribution,
    sbl_grouped_limit,
    spc_i_mr,
    syl_grouped_limit,
)
from app.infrastructure.sql_analysis_rule_service import SqlAnalysisRuleService
from app.infrastructure.sql_analytics_service import (
    SqlAnalyticsService,
    _condition_text,
    _finite_float,
    _hashes,
)

_CONTRACT_VERSION = "ANALYTICS_QUALITY_EVALUATION_V1"
_MAX_SYNC_UNITS = 250_000
_MAX_SYNC_MEASUREMENTS = 250_000
_MAX_SYNC_SPC_POINTS = 100_000
_MAX_VISUAL_POINTS_PER_GROUP = 10_000

_EXPECTED_ALGORITHMS = {
    "PAT_ROBUST_IQR": FORMAL_PAT_ALGORITHM_CODE,
    "SPC_I_MR": "SPC_I_MR_V1",
    "MARGIN_OOS": "SPEC_MARGIN_V1",
    "BIN_COOCCURRENCE": "BIN_COOCCURRENCE_UNIT_V1",
    "SBL_GROUPED_LIMIT": "SBL_GROUPED_LIMIT_V1",
    "SYL_GROUPED_LIMIT": "SYL_GROUPED_LIMIT_V1",
    "PASS_FAIL_DISTRIBUTION": "PASS_FAIL_DISTRIBUTION_V1",
}


def _statement(sql: str, expanding: tuple[str, ...] = ()):
    statement = text(sql)
    if expanding:
        statement = statement.bindparams(
            *(bindparam(name, expanding=True) for name in expanding)
        )
    return statement


def _calculation_hash(
    request: QualityEvaluationRequest,
    context_hash: str,
    parameters_sha256: str,
) -> str:
    payload = {
        "analysis": request.analysis.value,
        "bin_type": request.bin_type.value if request.bin_type is not None else None,
        "context_hash": context_hash,
        "group_by": request.group_by.value,
        "parameters_sha256": parameters_sha256,
        "rule": asdict(
            QualityRuleProvenance(
                request.rule.rule_code,
                request.rule.version_code,
                _EXPECTED_ALGORITHMS[request.analysis.value],
                "APPROVED",
                "ENABLED",
                "",
            )
        ),
        "spc_order": (
            request.spc_order.value if request.spc_order is not None else None
        ),
        "spc_phase": (
            request.spc_phase.value if request.spc_phase is not None else None
        ),
    }
    payload["rule"].pop("parameters_sha256")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _parameter_signature(row: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        str(row["canonical_parameter_code"] or ""),
        str(row["step_code"]),
        int(row["sequence_no"]),
        str(row["unit_code"] or ""),
        _finite_float(row["program_lsl"], field="program LSL"),
        _finite_float(row["program_usl"], field="program USL"),
        _condition_text(row["condition_json"]),
    )


def _group_key(row: Mapping[str, Any], group_by: QualityGroupBy) -> str:
    prefix = f"D:{int(row['dataset_id'])}:V:{int(row['version_no'])}"
    if group_by == QualityGroupBy.DATASET:
        return prefix
    if group_by == QualityGroupBy.LOT:
        value = row.get("lot_id")
        label = "LOT"
    elif group_by == QualityGroupBy.WAFER:
        lot = str(row.get("lot_id") or "").strip()
        wafer = str(row.get("wafer_id") or "").strip()
        if not lot or not wafer:
            raise DomainError(
                "ANALYSIS_WAFER_IDENTITY_REQUIRED",
                "WAFER grouping requires non-empty Lot and Wafer identity",
                409,
            )
        return f"{prefix}|LOT:{lot}|WAFER:{wafer}"
    elif group_by == QualityGroupBy.RUN:
        value = row.get("run_id")
        label = "RUN"
    elif group_by == QualityGroupBy.TESTER:
        value = row.get("tester_id")
        label = "TESTER"
    elif group_by == QualityGroupBy.PROGRAM:
        value = row.get("program_version")
        label = "PROGRAM"
    else:
        value = row.get("test_condition")
        label = "CONDITION"
    normalized = str(value or "").strip()
    if not normalized:
        raise DomainError(
            "ANALYSIS_GROUP_IDENTITY_MISSING",
            f"{group_by.value} grouping identity is unavailable",
            409,
        )
    return f"{prefix}|{label}:{normalized}"


def _sample_spc_points(
    points: tuple[SpcPoint, ...],
) -> tuple[tuple[SpcPoint, ...], AnalyticsSamplingSummary]:
    signals = tuple(point for point in points if point.rule_hits)
    ordinary = tuple(point for point in points if not point.rule_hits)
    budget = max(0, _MAX_VISUAL_POINTS_PER_GROUP - len(signals))
    selected = {*signals, *ordinary[:budget]}
    returned = tuple(point for point in points if point in selected)
    sampled = len(returned) < len(points)
    return returned, AnalyticsSamplingSummary(
        sampled,
        "ALL_RULE_HITS_THEN_SEQUENCE_V1" if sampled else None,
        len(points),
        len(returned),
        len(signals),
    )


def _sample_margin_points(
    points: tuple[MarginPoint, ...],
) -> tuple[tuple[MarginPoint, ...], AnalyticsSamplingSummary]:
    out_of_spec = tuple(point for point in points if point.out_of_spec)
    in_spec = tuple(point for point in points if not point.out_of_spec)
    budget = max(0, _MAX_VISUAL_POINTS_PER_GROUP - len(out_of_spec))
    selected = {*out_of_spec, *in_spec[:budget]}
    returned = tuple(point for point in points if point in selected)
    sampled = len(returned) < len(points)
    return returned, AnalyticsSamplingSummary(
        sampled,
        "ALL_OOS_THEN_UNIT_MEASUREMENT_V1" if sampled else None,
        len(points),
        len(returned),
        len(out_of_spec),
    )


class SqlQualityEvaluationService:
    """Approved-rule-only formal quality calculations over Canonical facts."""

    def __init__(
        self,
        engine: Engine,
        rule_service: SqlAnalysisRuleService | None = None,
    ) -> None:
        self._engine = engine
        self._analytics = SqlAnalyticsService(engine)
        self._rules = rule_service or SqlAnalysisRuleService(engine)

    @staticmethod
    def _require_supported_rule_semantics(
        request: QualityEvaluationRequest,
        parameters: AnalysisRuleParameters,
    ) -> None:
        if parameters.retest_policy != RetestPolicy.EACH_ATTEMPT:
            raise DomainError(
                "ANALYSIS_RETEST_POLICY_UNSUPPORTED",
                "formal quality V1 requires EACH_ATTEMPT; physical retest collapse is not approved",
                409,
            )
        if parameters.outlier_policy != OutlierPolicy.MARK_ONLY:
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "formal quality V1 only supports MARK_ONLY without silently changing the population",
                409,
            )
        subgroup = str(parameters.subgroup_dimension or "").strip().upper()
        if subgroup != request.group_by.value:
            raise DomainError(
                "ANALYSIS_RULE_SCOPE_MISMATCH",
                "request grouping does not match the approved rule subgroup",
                409,
            )
        if (
            parameters.missing_value_policy
            == MissingValuePolicy.PAIRWISE_EXCLUDE_AND_COUNT
        ):
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "pairwise missing-value policy is not valid for a univariate or Bin quality method",
                409,
            )
        if request.analysis.value == "PAT_ROBUST_IQR" and (
            parameters.lower_multiplier is None or parameters.upper_multiplier is None
        ):
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "approved PAT rule is missing positive lower/upper multipliers",
                409,
            )
        if request.analysis.value == "PAT_ROBUST_IQR" and (
            parameters.lower_multiplier != 6.0 or parameters.upper_multiplier != 6.0
        ):
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "PAT_SHARED_IQR_1_35_V1 requires lower_multiplier=6 and upper_multiplier=6",
                409,
            )
        if request.analysis.value == "SPC_I_MR" and (
            parameters.sigma_definition != SigmaDefinition.POOLED_WITHIN
        ):
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "SPC_I_MR_V1 requires the approved POOLED_WITHIN moving-range policy",
                409,
            )
        if request.analysis.value == "SPC_I_MR":
            if parameters.spc_run_rule_mode is None:
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "SPC_I_MR_V1 requires an explicit approved run-rule mode",
                    409,
                )
            basic_values = (
                parameters.spc_consecutive_beyond_count,
                parameters.spc_consecutive_beyond_sigma,
                parameters.spc_same_side_run_length,
                parameters.spc_monotonic_run_length,
            )
            if parameters.spc_run_rule_mode == SpcRunRuleMode.BASIC and any(
                value is None for value in basic_values
            ):
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "SPC BASIC run rules require every approved threshold",
                    409,
                )
            if parameters.spc_run_rule_mode == SpcRunRuleMode.NONE and any(
                value is not None for value in basic_values
            ):
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "SPC NONE run-rule mode cannot carry hidden thresholds",
                    409,
                )
        if request.analysis.value == "MARGIN_OOS" and (
            parameters.equality_is_in_spec is None
        ):
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "approved Margin rule is missing equality boundary semantics",
                409,
            )
        if request.analysis.value == "BIN_COOCCURRENCE" and (
            parameters.sparse_matrix_minimum_count is None
        ):
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "approved Bin cooccurrence rule is missing the sparse threshold",
                409,
            )
        if request.analysis.value == "SBL_GROUPED_LIMIT" and (
            parameters.upper_multiplier is None
            or parameters.sigma_definition != SigmaDefinition.SAMPLE
        ):
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "SBL_GROUPED_LIMIT_V1 requires an upper multiplier and SAMPLE sigma",
                409,
            )
        if request.analysis.value == "SYL_GROUPED_LIMIT":
            if (
                parameters.lower_multiplier is None
                or parameters.sigma_definition != SigmaDefinition.SAMPLE
                or parameters.limit_rounding_policy is None
            ):
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "SYL_GROUPED_LIMIT_V1 requires lower multiplier, SAMPLE sigma and explicit rounding",
                    409,
                )
            if (
                parameters.limit_rounding_policy == LimitRoundingPolicy.NONE
                and parameters.limit_rounding_step is not None
            ) or (
                parameters.limit_rounding_policy != LimitRoundingPolicy.NONE
                and parameters.limit_rounding_step is None
            ):
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "approved SYL rounding policy and step are inconsistent",
                    409,
                )
        if request.analysis.value == "PASS_FAIL_DISTRIBUTION" and (
            parameters.histogram_bin_count is None
        ):
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "PASS_FAIL_DISTRIBUTION_V1 requires an approved histogram bin count",
                409,
            )

    @staticmethod
    def _workload_guard(*, units: int, measurements: int, analysis: str) -> None:
        point_limit = (
            _MAX_SYNC_SPC_POINTS if analysis == "SPC_I_MR" else _MAX_SYNC_MEASUREMENTS
        )
        if units > _MAX_SYNC_UNITS or measurements > point_limit:
            raise DomainError(
                "ANALYSIS_WORKLOAD_LIMIT_EXCEEDED",
                "formal quality workload exceeds the synchronous boundary; submit a pinned Worker job",
                413,
                details=[
                    {
                        "input_units": units,
                        "candidate_measurements": measurements,
                        "unit_limit": _MAX_SYNC_UNITS,
                        "measurement_limit": point_limit,
                        "recommended_execution": "WORKER",
                    }
                ],
            )

    def _resolve_rule(
        self,
        request: QualityEvaluationRequest,
        stage: str,
        scopes: set[tuple[int | None, int | None]],
    ) -> tuple[AnalysisRuleParameters, QualityRuleProvenance]:
        raw_contracts: list[dict[str, Any]] = []
        parameter = (
            request.parameters[0]
            if request.parameters
            else request.bin_type.value
            if request.bin_type is not None
            else None
        )
        for supplier_id, product_id in sorted(
            scopes, key=lambda item: (item[0] or 0, item[1] or 0)
        ):
            raw_contracts.append(
                self._rules.approved_rule_parameters(
                    rule_code=request.rule.rule_code,
                    version_code=request.rule.version_code,
                    test_stage=stage,
                    expected_algorithm_code=_EXPECTED_ALGORITHMS[
                        request.analysis.value
                    ],
                    supplier_id=supplier_id,
                    product_id=product_id,
                    parameter=parameter,
                )
            )
        if not raw_contracts:
            raise DomainError(
                "ANALYSIS_CONTEXT_INVALID",
                "selected Dataset has no test-run scope",
                409,
            )
        canonical = {
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for item in raw_contracts
        }
        if len(canonical) != 1:
            raise DomainError(
                "ANALYSIS_RULE_SCOPE_MISMATCH",
                "approved rule parameters differ across selected Dataset scopes",
                409,
            )
        raw = raw_contracts[0]
        try:
            parameters = AnalysisRuleParameters.model_validate(raw)
        except ValueError as exc:
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "approved quality rule parameters do not match the typed server contract",
                409,
            ) from exc
        self._require_supported_rule_semantics(request, parameters)
        serialized = next(iter(canonical))
        return parameters, QualityRuleProvenance(
            request.rule.rule_code,
            request.rule.version_code,
            _EXPECTED_ALGORITHMS[request.analysis.value],
            "APPROVED",
            "ENABLED",
            hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        )

    def _unit_rows(
        self,
        connection: Connection,
        request: QualityEvaluationRequest,
        context: Mapping[str, Any],
    ) -> tuple[
        tuple[Mapping[str, Any], ...],
        tuple[Mapping[str, Any], ...],
        str,
        dict[str, object],
        tuple[str, ...],
    ]:
        source_rows = self._analytics._source_rows(connection, context)
        item_rows = self._analytics._item_rows(connection, context)
        source_run_ids = self._analytics._selected_run_ids(request, source_rows)
        condition_item_ids = self._analytics._selected_condition_item_ids(
            request, item_rows
        )
        filter_sql, filter_parameters, expanding = self._analytics._filter_sql(
            request,
            source_run_ids=source_run_ids,
            condition_item_ids=condition_item_ids,
        )
        rows = tuple(
            connection.execute(
                _statement(
                    "SELECT :dataset AS dataset_id,:version AS version_no,"
                    "ur.unit_id,ur.logical_unit_key,ur.attempt_no,ur.unit_sequence,"
                    "tr.run_id,tr.run_attempt_no,tr.supplier_id,tr.product_id,tr.lot_id,"
                    "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,tr.tester_id,"
                    "pv.version_code AS program_version,ur.soft_bin,ur.hard_bin,"
                    "ur.overall_result,ur.source_row_no "
                    + self._analytics._base_join()
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + filter_sql
                    + " ORDER BY tr.run_id,ISNULL(ur.unit_sequence,ur.unit_id),ur.unit_id",
                    expanding,
                ),
                {
                    "dataset": int(context["dataset_id"]),
                    "version": int(context["version_no"]),
                    **filter_parameters,
                },
            )
            .mappings()
            .all()
        )
        return rows, item_rows, filter_sql, filter_parameters, expanding

    def _parameter_identity(
        self,
        request: QualityEvaluationRequest,
        work: list[dict[str, Any]],
    ) -> QualityParameterIdentity | None:
        if not request.parameters:
            return None
        parameter = request.parameters[0]
        signatures: set[tuple[object, ...]] = set()
        for item in work:
            item_rows = item["item_rows"]
            item["parameter_ids"] = self._analytics._parameter_ids(
                item_rows, (parameter,)
            )
            signatures.update(
                _parameter_signature(row)
                for row in item_rows
                if str(row["raw_item_name"]) == parameter
            )
        if len(signatures) != 1:
            raise DomainError(
                "ANALYSIS_PARAMETER_INCOMPATIBLE",
                "selected parameter does not have one exact identity across all Datasets",
                409,
            )
        signature = next(iter(signatures))
        return QualityParameterIdentity(
            parameter,
            str(signature[0]) or None,
            str(signature[1]),
            int(signature[2]),
            str(signature[3]) or None,
            str(signature[6]) if signature[6] is not None else None,
            float(signature[4]) if signature[4] is not None else None,
            float(signature[5]) if signature[5] is not None else None,
        )

    def _measurement_rows(
        self,
        connection: Connection,
        item: dict[str, Any],
        identity: QualityParameterIdentity,
    ) -> tuple[Mapping[str, Any], ...]:
        context = item["context"]
        rows = tuple(
            connection.execute(
                _statement(
                    "SELECT :dataset AS dataset_id,:version AS version_no,"
                    "ur.unit_id,ur.logical_unit_key,ur.attempt_no,ur.unit_sequence,"
                    "tr.run_id,tr.run_attempt_no,tr.supplier_id,tr.product_id,tr.lot_id,"
                    "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,tr.tester_id,"
                    "pv.version_code AS program_version,m.measurement_id,m.value_numeric,"
                    "m.measurement_status,ur.overall_result,tid.condition_json "
                    + self._analytics._base_join()
                    + "LEFT JOIN test.measurement m ON m.unit_id=ur.unit_id "
                    "AND m.test_item_id IN :parameter_ids "
                    "LEFT JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                    "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + item["filter_sql"]
                    + " ORDER BY tr.run_id,ISNULL(ur.unit_sequence,ur.unit_id),ur.unit_id,m.measurement_id",
                    item["expanding"] + ("parameter_ids",),
                ),
                {
                    "dataset": int(context["dataset_id"]),
                    "version": int(context["version_no"]),
                    "parameter_ids": item["parameter_ids"],
                    **item["filter_parameters"],
                },
            )
            .mappings()
            .all()
        )
        by_unit: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            if row["measurement_id"] is not None:
                by_unit[int(row["unit_id"])].append(row)
        ambiguous = sorted(
            unit_id for unit_id, values in by_unit.items() if len(values) > 1
        )
        if ambiguous:
            raise DomainError(
                "ANALYSIS_PARAMETER_INCOMPATIBLE",
                "more than one measurement resolved for a physical Unit and exact parameter",
                409,
                details=[{"unit_ids": ambiguous[:20]}],
            )
        normalized: list[Mapping[str, Any]] = []
        for row in rows:
            mutable = dict(row)
            mutable["test_condition"] = identity.test_condition
            normalized.append(mutable)
        return tuple(normalized)

    @staticmethod
    def _valid_value(row: Mapping[str, Any]) -> float | None:
        if row.get("measurement_status") != "MEASURED":
            return None
        return _finite_float(row.get("value_numeric"), field="quality measurement")

    def _formal_spec(
        self,
        connection: Connection,
        item: dict[str, Any],
        identity: QualityParameterIdentity,
    ) -> tuple[int, str, float | None, float | None, str | None, str | None]:
        context = item["context"]
        stage = str(context["test_stage"])
        if stage == "CP":
            joins = (
                "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=:dataset_spec "
                "AND ss.status='RELEASED' "
                "AND (ss.effective_from_utc IS NULL OR ss.effective_from_utc<=COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "AND (ss.effective_to_utc IS NULL OR ss.effective_to_utc>COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "LEFT JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
                "AND si.test_item_id=tid.test_item_id "
                "LEFT JOIN mdm.spec_binding sb ON 1=0 "
                "LEFT JOIN mdm.scope_priority sp ON 1=0 "
            )
        else:
            joins = (
                "LEFT JOIN mdm.spec_binding sb ON "
                "(sb.program_version_id IS NULL OR sb.program_version_id=tr.program_version_id) "
                "AND (sb.product_id IS NULL OR sb.product_id=tr.product_id) "
                "AND (sb.supplier_id IS NULL OR sb.supplier_id=tr.supplier_id) "
                "AND (sb.test_stage IS NULL OR sb.test_stage=tr.test_stage) "
                "AND (sb.effective_from_utc IS NULL OR sb.effective_from_utc<=COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "AND (sb.effective_to_utc IS NULL OR sb.effective_to_utc>COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "LEFT JOIN mdm.scope_priority sp ON sp.scope_code=sb.scope_code "
                "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=sb.spec_set_id "
                "AND ss.status='RELEASED' "
                "AND (ss.effective_from_utc IS NULL OR ss.effective_from_utc<=COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "AND (ss.effective_to_utc IS NULL OR ss.effective_to_utc>COALESCE(tr.started_at_utc,pr.started_at_utc)) "
                "LEFT JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
                "AND si.test_item_id=tid.test_item_id "
            )
        rows = tuple(
            connection.execute(
                _statement(
                    "SELECT DISTINCT tr.run_id,tr.lot_id,tr.test_stage,COALESCE(tr.started_at_utc,pr.started_at_utc) AS event_at_utc,"
                    "tr.program_version_id AS run_program_version_id,tid.program_version_id AS item_program_version_id,"
                    "tid.test_item_id,sb.spec_binding_id,sp.priority AS scope_priority,ss.spec_set_id,"
                    "ss.version_code,si.spec_item_id,si.unit_code,si.lsl,si.usl,"
                    "si.lower_operator,si.upper_operator,si.condition_json "
                    + self._analytics._base_join()
                    + "JOIN ingestion.processing_run pr ON pr.processing_run_id=dvr.processing_run_id "
                    + "JOIN mdm.test_item_definition tid ON "
                    "tid.program_version_id=tr.program_version_id "
                    "AND tid.test_item_id IN :parameter_ids "
                    + joins
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + item["filter_sql"],
                    item["expanding"] + ("parameter_ids",),
                ),
                {
                    "dataset": int(context["dataset_id"]),
                    "version": int(context["version_no"]),
                    "dataset_spec": context["spec_set_id"],
                    "parameter_ids": item["parameter_ids"],
                    **item["filter_parameters"],
                },
            )
            .mappings()
            .all()
        )
        resolved = resolve_released_formal_spec(
            rows,
            parameter=identity.name,
            identity_unit=identity.unit,
            identity_condition=identity.test_condition,
        )
        if not resolved.resolved:
            raise DomainError(
                "ANALYSIS_SPEC_INCOMPATIBLE",
                "formal Spec is missing or ambiguous for one or more selected historic scopes",
                409,
                details=[{"reason_codes": list(resolved.reason_codes)}],
            )
        return (
            resolved.spec_set_ids[0],
            resolved.spec_versions[0].rsplit(":", 1)[1],
            resolved.lsl,
            resolved.usl,
            resolved.lower_operator,
            resolved.upper_operator,
        )

    def _bin_rows(
        self, connection: Connection, item: dict[str, Any], bin_type: str
    ) -> tuple[Mapping[str, Any], ...]:
        context = item["context"]
        if bin_type == "ALL_MAPPED_FAILURE":
            bin_join_filter = ""
            bin_parameters: dict[str, object] = {}
        else:
            bin_join_filter = " AND ube.bin_type=:bin_type"
            bin_parameters = {"bin_type": bin_type}
        return tuple(
            connection.execute(
                _statement(
                    "SELECT :dataset AS dataset_id,:version AS version_no,"
                    "ur.unit_id,tr.run_id,tr.lot_id,"
                    "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,tr.tester_id,"
                    "pv.version_code AS program_version,ube.unit_bin_evaluation_id,"
                    "ube.mapping_status,ube.bin_mapping_set_id,ube.bin_definition_id,"
                    "ube.raw_bin_code,ube.is_pass_snapshot,ube.bin_type,bms.version_code AS bin_version "
                    + self._analytics._base_join()
                    + "LEFT JOIN test.unit_bin_evaluation ube ON ube.unit_id=ur.unit_id "
                    + bin_join_filter
                    + " "
                    "LEFT JOIN mdm.bin_mapping_set bms "
                    "ON bms.bin_mapping_set_id=ube.bin_mapping_set_id "
                    "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + item["filter_sql"]
                    + " ORDER BY ur.unit_id,ube.unit_bin_evaluation_id",
                    item["expanding"],
                ),
                {
                    "dataset": int(context["dataset_id"]),
                    "version": int(context["version_no"]),
                    **bin_parameters,
                    **item["filter_parameters"],
                },
            )
            .mappings()
            .all()
        )

    @staticmethod
    def _validated_bins(
        rows: tuple[Mapping[str, Any], ...],
    ) -> tuple[dict[int, tuple[Mapping[str, Any], ...]], int]:
        by_unit: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        all_units: set[int] = set()
        for row in rows:
            unit_id = int(row["unit_id"])
            all_units.add(unit_id)
            if row["unit_bin_evaluation_id"] is not None:
                by_unit[unit_id].append(row)
        validated: dict[int, tuple[Mapping[str, Any], ...]] = {}
        for unit_id, values in by_unit.items():
            by_type: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for row in values:
                by_type[str(row["bin_type"])].append(row)
            valid_rows: list[Mapping[str, Any]] = []
            invalid_mapping = False
            for bin_type, type_rows in by_type.items():
                if len(type_rows) != 1:
                    raise DomainError(
                        "ANALYSIS_BIN_MAPPING_AMBIGUOUS",
                        f"multiple {bin_type} evaluation snapshots exist for a physical Unit",
                        409,
                    )
                row = type_rows[0]
                if (
                    row["mapping_status"] == "MATCHED"
                    and row["bin_mapping_set_id"] is not None
                    and row["bin_definition_id"] is not None
                    and row["is_pass_snapshot"] is not None
                ):
                    valid_rows.append(row)
                else:
                    invalid_mapping = True
            valid = tuple(valid_rows)
            if valid and not invalid_mapping:
                validated[unit_id] = valid
        return validated, len(all_units) - len(validated)

    def analyze(self, request: QualityEvaluationRequest) -> QualityEvaluationResult:
        filter_summary = _hashes(request)
        work: list[dict[str, Any]] = []
        input_units = 0
        included_units = 0
        scopes: set[tuple[int | None, int | None]] = set()
        selected_spec_versions: set[str] = set()
        selected_bin_versions: set[str] = set()
        with self._engine.connect() as connection:
            context_rows = self._analytics._context_rows(connection, request)
            dataset_context = self._analytics._dataset_context(context_rows)
            for context in context_rows:
                scope_rows = (
                    connection.execute(
                        text(
                            "SELECT DISTINCT tr.supplier_id,tr.product_id "
                            "FROM dataset.dataset_version dv "
                            "JOIN dataset.dataset_version_run dvr "
                            "ON dvr.dataset_version_id=dv.dataset_version_id "
                            "JOIN test.test_run tr "
                            "ON tr.processing_run_id=dvr.processing_run_id "
                            "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                        ),
                        {
                            "dataset": int(context["dataset_id"]),
                            "version": int(context["version_no"]),
                        },
                    )
                    .mappings()
                    .all()
                )
                scopes.update(
                    (int(row["supplier_id"]), int(row["product_id"]))
                    for row in scope_rows
                )
            parameters, rule = self._resolve_rule(
                request, dataset_context.test_stage, scopes
            )
            calculation_hash = _calculation_hash(
                request, filter_summary.context_hash, rule.parameters_sha256
            )
            base_rule_context = self._analytics._rule_context(
                connection, context_rows, request
            )
            for context in context_rows:
                input_units += int(
                    connection.execute(
                        text(
                            "SELECT COUNT_BIG(*) FROM dataset.dataset_version dv "
                            "JOIN dataset.dataset_version_run dvr "
                            "ON dvr.dataset_version_id=dv.dataset_version_id "
                            "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                            "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                            "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                        ),
                        {
                            "dataset": int(context["dataset_id"]),
                            "version": int(context["version_no"]),
                        },
                    ).scalar_one()
                )
                units, item_rows, filter_sql, filter_parameters, expanding = (
                    self._unit_rows(connection, request, context)
                )
                included_units += len(units)
                work.append(
                    {
                        "context": context,
                        "units": units,
                        "item_rows": item_rows,
                        "filter_sql": filter_sql,
                        "filter_parameters": filter_parameters,
                        "expanding": expanding,
                    }
                )
            self._workload_guard(
                units=included_units,
                measurements=included_units if request.parameters else 0,
                analysis=request.analysis.value,
            )
            identity = self._parameter_identity(request, work)

            pat_results: list[PatGroupResult] = []
            spc_results: list[SpcGroupResult] = []
            margin_results: list[MarginGroupResult] = []
            cooccurrence_results: list[BinCooccurrenceCell] = []
            sbl_results: list[SblBinLimit] = []
            syl_results: list[SylDatasetLimit] = []
            pass_fail_results: list[PassFailDistributionGroup] = []
            included_measurements = 0
            missing_measurements = 0
            warnings: set[str] = set()

            if identity is not None:
                all_measurements: list[Mapping[str, Any]] = []
                specs: dict[
                    tuple[int, int],
                    tuple[
                        int,
                        str,
                        float | None,
                        float | None,
                        str | None,
                        str | None,
                    ],
                ] = {}
                for item in work:
                    rows = self._measurement_rows(connection, item, identity)
                    all_measurements.extend(rows)
                    if request.analysis.value == "MARGIN_OOS":
                        context = item["context"]
                        spec = self._formal_spec(connection, item, identity)
                        specs[
                            (int(context["dataset_id"]), int(context["version_no"]))
                        ] = spec
                        selected_spec_versions.add(f"SPEC:{spec[0]}:{spec[1]}")
                valid_rows = [
                    row
                    for row in all_measurements
                    if self._valid_value(row) is not None
                ]
                if request.analysis.value == "PASS_FAIL_DISTRIBUTION":
                    included_measurements = sum(
                        self._valid_value(row) is not None
                        and str(row.get("overall_result") or "").upper()
                        in {"PASS", "FAIL"}
                        for row in all_measurements
                    )
                else:
                    included_measurements = len(valid_rows)
                missing_measurements = included_units - len(valid_rows)
                if (
                    parameters.missing_value_policy == MissingValuePolicy.FAIL_IF_ANY
                    and missing_measurements
                ):
                    raise DomainError(
                        "ANALYSIS_MISSING_VALUE_NOT_ALLOWED",
                        "approved rule rejects missing or non-measured values",
                        409,
                    )
                grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
                missing_by_group: dict[str, int] = defaultdict(int)
                for row in all_measurements:
                    key = _group_key(row, request.group_by)
                    if self._valid_value(row) is None:
                        missing_by_group[key] += 1
                    else:
                        grouped[key].append(row)

                if request.analysis.value == "PAT_ROBUST_IQR":
                    warnings.add(
                        f"{FORMAL_PAT_ADAPTER_CONTRACT_VERSION} uses the frozen shared "
                        "FT PAT semantics: linear quantiles, IQR/1.35, Median +/- "
                        "6 Sigma, six-decimal limits, MARK_ONLY and no implicit Spec "
                        f"clamp; adapter_sha256={FORMAL_PAT_ADAPTER_MANIFEST_SHA256}; "
                        f"source_sha256={FORMAL_PAT_SOURCE_SHA256}"
                    )
                    for key, rows in sorted(grouped.items()):
                        if len(rows) < parameters.minimum_sample_size:
                            pat_results.append(
                                PatGroupResult(
                                    int(rows[0]["dataset_id"]),
                                    int(rows[0]["version_no"]),
                                    key,
                                    len(rows),
                                    missing_by_group[key],
                                    None,
                                    None,
                                    None,
                                    None,
                                    None,
                                    None,
                                    None,
                                    0,
                                    None,
                                    "INSUFFICIENT_N",
                                    (),
                                )
                            )
                            continue
                        values = [float(self._valid_value(row)) for row in rows]
                        result = calculate_formal_pat(
                            values,
                            lower_multiplier=float(parameters.lower_multiplier),
                            upper_multiplier=float(parameters.upper_multiplier),
                        )
                        evidence = tuple(
                            QualityEvidencePoint(
                                int(rows[index]["dataset_id"]),
                                int(rows[index]["version_no"]),
                                int(rows[index]["unit_id"]),
                                int(rows[index]["measurement_id"]),
                                values[index],
                                f"UNIT:{int(rows[index]['unit_id'])}",
                                "PAT_OUTLIER",
                            )
                            for index in result.outlier_indexes
                        )
                        pat_results.append(
                            PatGroupResult(
                                int(rows[0]["dataset_id"]),
                                int(rows[0]["version_no"]),
                                key,
                                len(rows),
                                missing_by_group[key],
                                result.q1,
                                result.median,
                                result.q3,
                                result.iqr,
                                result.robust_sigma,
                                result.lower_limit,
                                result.upper_limit,
                                len(evidence),
                                len(evidence) / len(rows),
                                "ZERO_DISPERSION"
                                if result.iqr == 0.0
                                else "ASSESSABLE",
                                evidence,
                            )
                        )
                elif request.analysis.value == "SPC_I_MR":
                    for key, rows in sorted(grouped.items()):
                        if len(rows) < parameters.minimum_sample_size:
                            spc_results.append(
                                SpcGroupResult(
                                    int(rows[0]["dataset_id"]),
                                    int(rows[0]["version_no"]),
                                    key,
                                    len(rows),
                                    missing_by_group[key],
                                    None,
                                    None,
                                    None,
                                    None,
                                    None,
                                    True,
                                    filter_summary.context_hash,
                                    "INSUFFICIENT_N",
                                    (),
                                )
                            )
                            continue
                        if any(row["unit_sequence"] is None for row in rows):
                            raise DomainError(
                                "SPC_ORDER_FIELD_MISSING",
                                "SPC I-MR requires unit_sequence for every included value",
                                409,
                            )
                        try:
                            kernel = spc_i_mr(
                                [
                                    OrderedKernelValue(
                                        int(row["unit_sequence"]),
                                        float(self._valid_value(row)),
                                        f"UNIT:{int(row['unit_id'])}",
                                    )
                                    for row in rows
                                ],
                                run_rule_mode=parameters.spc_run_rule_mode.value,
                                consecutive_beyond_count=parameters.spc_consecutive_beyond_count,
                                consecutive_beyond_sigma=parameters.spc_consecutive_beyond_sigma,
                                same_side_run_length=parameters.spc_same_side_run_length,
                                monotonic_run_length=parameters.spc_monotonic_run_length,
                            )
                        except ValueError as exc:
                            raise DomainError(
                                "SPC_ORDER_FIELD_AMBIGUOUS", str(exc), 409
                            ) from exc
                        all_spc_points = tuple(
                            SpcPoint(
                                point.sequence,
                                point.value,
                                point.moving_range,
                                point.drilldown_key,
                                point.rule_hits,
                            )
                            for point in kernel.points
                        )
                        sampled_spc_points, spc_sampling = _sample_spc_points(
                            all_spc_points
                        )
                        spc_results.append(
                            SpcGroupResult(
                                int(rows[0]["dataset_id"]),
                                int(rows[0]["version_no"]),
                                key,
                                len(rows),
                                missing_by_group[key],
                                kernel.center_line,
                                kernel.lower_control_limit,
                                kernel.upper_control_limit,
                                kernel.mr_bar,
                                kernel.mr_upper_control_limit,
                                True,
                                filter_summary.context_hash,
                                "ZERO_DISPERSION"
                                if kernel.mr_bar == 0.0
                                else "ASSESSABLE",
                                sampled_spc_points,
                                spc_sampling,
                            )
                        )
                    if parameters.spc_run_rule_mode == SpcRunRuleMode.NONE:
                        warnings.add(
                            "SPC_I_MR_V1 run-rule mode is explicitly NONE; no run or trend rule is inferred"
                        )
                    else:
                        warnings.add(
                            "SPC_I_MR_V1 executes only the exact approved BASIC consecutive-beyond, same-side and monotonic-run thresholds"
                        )
                elif request.analysis.value == "PASS_FAIL_DISTRIBUTION":
                    rows_by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(
                        list
                    )
                    for row in all_measurements:
                        rows_by_group[_group_key(row, request.group_by)].append(row)
                    for key, rows in sorted(rows_by_group.items()):
                        pass_values: list[tuple[float, str]] = []
                        fail_values: list[tuple[float, str]] = []
                        unknown_excluded = 0
                        abort_excluded = 0
                        other_excluded = 0
                        missing = 0
                        for row in rows:
                            result = str(row.get("overall_result") or "").upper()
                            value = self._valid_value(row)
                            if value is None:
                                missing += 1
                                continue
                            point = (float(value), f"UNIT:{int(row['unit_id'])}")
                            if result == "PASS":
                                pass_values.append(point)
                            elif result == "FAIL":
                                fail_values.append(point)
                            elif result == "UNKNOWN":
                                unknown_excluded += 1
                            elif result == "ABORT":
                                abort_excluded += 1
                            else:
                                other_excluded += 1
                        valid_n = len(pass_values) + len(fail_values)
                        dataset_id = int(rows[0]["dataset_id"])
                        version_no = int(rows[0]["version_no"])
                        if valid_n < parameters.minimum_sample_size:
                            pass_fail_results.append(
                                PassFailDistributionGroup(
                                    dataset_id,
                                    version_no,
                                    key,
                                    len(pass_values),
                                    len(fail_values),
                                    unknown_excluded,
                                    abort_excluded,
                                    other_excluded,
                                    missing,
                                    None,
                                    None,
                                    None,
                                    None,
                                    "INSUFFICIENT_N",
                                    (),
                                )
                            )
                            continue
                        kernel = pass_fail_distribution(
                            pass_values,
                            fail_values,
                            bin_count=int(parameters.histogram_bin_count),
                        )
                        pass_fail_results.append(
                            PassFailDistributionGroup(
                                dataset_id,
                                version_no,
                                key,
                                len(pass_values),
                                len(fail_values),
                                unknown_excluded,
                                abort_excluded,
                                other_excluded,
                                missing,
                                kernel.pass_mean,
                                kernel.fail_mean,
                                kernel.minimum,
                                kernel.maximum,
                                "ZERO_DISPERSION"
                                if kernel.minimum == kernel.maximum
                                else "ASSESSABLE",
                                tuple(
                                    PassFailHistogramBin(
                                        item.bin_index,
                                        item.lower,
                                        item.upper,
                                        item.pass_count,
                                        item.fail_count,
                                        item.pass_drilldown_keys,
                                        item.fail_drilldown_keys,
                                    )
                                    for item in kernel.bins
                                ),
                            )
                        )
                    warnings.add(
                        "PASS_FAIL_DISTRIBUTION_V1 compares only measured PASS versus FAIL Units; UNKNOWN, ABORT and other result states are excluded and counted"
                    )
                else:
                    for key, rows in sorted(grouped.items()):
                        dataset_key = (
                            int(rows[0]["dataset_id"]),
                            int(rows[0]["version_no"]),
                        )
                        (
                            spec_id,
                            spec_version,
                            lsl,
                            usl,
                            lower_operator,
                            upper_operator,
                        ) = specs[dataset_key]
                        approved_equality = bool(parameters.equality_is_in_spec)
                        operator_equalities = {
                            lower_operator != ">"
                            if lower_operator is not None
                            else approved_equality,
                            upper_operator != "<"
                            if upper_operator is not None
                            else approved_equality,
                        }
                        if (
                            len(operator_equalities) != 1
                            or next(iter(operator_equalities)) != approved_equality
                        ):
                            raise DomainError(
                                "ANALYSIS_RULE_SPEC_OPERATOR_MISMATCH",
                                "approved Margin equality semantics do not match the effective Formal Spec operators",
                                409,
                            )
                        points: list[MarginPoint] = []
                        for row in rows:
                            value = float(self._valid_value(row))
                            kernel = margin_oos(
                                value,
                                lsl=lsl,
                                usl=usl,
                                equality_is_in_spec=approved_equality,
                            )
                            points.append(
                                MarginPoint(
                                    int(row["dataset_id"]),
                                    int(row["version_no"]),
                                    int(row["unit_id"]),
                                    int(row["measurement_id"]),
                                    value,
                                    kernel.lower_margin,
                                    kernel.upper_margin,
                                    kernel.nearest_margin,
                                    kernel.out_of_spec,
                                    f"UNIT:{int(row['unit_id'])}",
                                )
                            )
                        oos = sum(point.out_of_spec for point in points)
                        sampled_margin_points, margin_sampling = _sample_margin_points(
                            tuple(points)
                        )
                        margin_results.append(
                            MarginGroupResult(
                                dataset_key[0],
                                dataset_key[1],
                                key,
                                spec_id,
                                spec_version,
                                (
                                    "TWO_SIDED"
                                    if lsl is not None and usl is not None
                                    else "LOWER_ONLY"
                                    if lsl is not None
                                    else "UPPER_ONLY"
                                ),
                                lsl,
                                usl,
                                len(points),
                                missing_by_group[key],
                                oos,
                                oos / len(points) if points else None,
                                min(
                                    (point.nearest_margin for point in points),
                                    default=None,
                                ),
                                sampled_margin_points,
                                margin_sampling,
                            )
                        )
            elif request.analysis.value == "SYL_GROUPED_LIMIT":
                included_measurements = 0
                for item in work:
                    context = item["context"]
                    grouped_units: dict[str, list[Mapping[str, Any]]] = defaultdict(
                        list
                    )
                    for raw_unit in item["units"]:
                        unit = dict(raw_unit)
                        unit["test_condition"] = None
                        grouped_units[_group_key(unit, request.group_by)].append(unit)
                    rates: dict[str, float] = {}
                    group_rows: list[SylGroupYield] = []
                    excluded_result_count = 0
                    for group, units in sorted(grouped_units.items()):
                        pass_units = tuple(
                            int(unit["unit_id"])
                            for unit in units
                            if str(unit.get("overall_result") or "").upper() == "PASS"
                        )
                        fail_units = tuple(
                            int(unit["unit_id"])
                            for unit in units
                            if str(unit.get("overall_result") or "").upper() == "FAIL"
                        )
                        unknown_count = sum(
                            str(unit.get("overall_result") or "").upper() == "UNKNOWN"
                            for unit in units
                        )
                        abort_count = sum(
                            str(unit.get("overall_result") or "").upper() == "ABORT"
                            for unit in units
                        )
                        other_count = (
                            len(units)
                            - len(pass_units)
                            - len(fail_units)
                            - unknown_count
                            - abort_count
                        )
                        excluded_result_count += (
                            unknown_count + abort_count + other_count
                        )
                        denominator = len(pass_units) + len(fail_units)
                        rate = len(pass_units) / denominator if denominator else None
                        if rate is not None:
                            rates[group] = rate
                        group_rows.append(
                            SylGroupYield(
                                group,
                                len(pass_units),
                                len(fail_units),
                                unknown_count,
                                abort_count,
                                other_count,
                                rate,
                                tuple(
                                    f"UNIT:{unit_id}"
                                    for unit_id in sorted((*pass_units, *fail_units))
                                ),
                            )
                        )
                    if (
                        parameters.missing_value_policy
                        == MissingValuePolicy.FAIL_IF_ANY
                        and excluded_result_count
                    ):
                        raise DomainError(
                            "ANALYSIS_RESULT_STATE_NOT_ALLOWED",
                            "approved SYL rule rejects UNKNOWN, ABORT or unclassified Unit results",
                            409,
                        )
                    dataset_id = int(context["dataset_id"])
                    version_no = int(context["version_no"])
                    rounding_policy = parameters.limit_rounding_policy.value
                    if len(rates) < parameters.minimum_sample_size:
                        syl_results.append(
                            SylDatasetLimit(
                                dataset_id,
                                version_no,
                                len(rates),
                                None,
                                None,
                                None,
                                None,
                                rounding_policy,
                                parameters.limit_rounding_step,
                                "INSUFFICIENT_N",
                                (),
                                tuple(group_rows),
                            )
                        )
                        continue
                    kernel = syl_grouped_limit(
                        rates,
                        lower_multiplier=float(parameters.lower_multiplier),
                        rounding_policy=rounding_policy,
                        rounding_step=parameters.limit_rounding_step,
                    )
                    syl_results.append(
                        SylDatasetLimit(
                            dataset_id,
                            version_no,
                            len(rates),
                            kernel.mean_yield,
                            kernel.sample_stddev,
                            kernel.raw_lower_limit,
                            kernel.lower_limit,
                            rounding_policy,
                            parameters.limit_rounding_step,
                            "ASSESSABLE",
                            kernel.below_limit_groups,
                            tuple(group_rows),
                        )
                    )
                warnings.add(
                    "SYL_GROUPED_LIMIT_V1 uses PASS/(PASS+FAIL), sample standard deviation (ddof=1), and only the approved explicit rounding policy"
                )
            else:
                included_measurements = 0
                bin_rows_by_work: list[
                    tuple[
                        dict[str, Any],
                        tuple[Mapping[str, Any], ...],
                        dict[int, tuple[Mapping[str, Any], ...]],
                        int,
                    ]
                ] = []
                for item in work:
                    raw_rows = self._bin_rows(connection, item, request.bin_type.value)
                    validated, missing = self._validated_bins(raw_rows)
                    mapping_versions = {
                        (
                            str(row["bin_type"]),
                            int(row["bin_mapping_set_id"]),
                            str(row["bin_version"]),
                        )
                        for rows in validated.values()
                        for row in rows
                    }
                    versions_by_type: dict[str, set[tuple[int, str]]] = defaultdict(set)
                    for bin_type, mapping_id, version in mapping_versions:
                        versions_by_type[bin_type].add((mapping_id, version))
                    if any(len(versions) > 1 for versions in versions_by_type.values()):
                        raise DomainError(
                            "ANALYSIS_BIN_MAPPING_INCOMPATIBLE",
                            "selected physical Units do not share one compatible Bin Mapping version per type",
                            409,
                        )
                    selected_bin_versions.update(
                        f"BIN:{mapping_id}:{version}"
                        for _, mapping_id, version in mapping_versions
                    )
                    bin_rows_by_work.append((item, raw_rows, validated, missing))
                    missing_measurements += missing
                if (
                    parameters.missing_value_policy == MissingValuePolicy.FAIL_IF_ANY
                    and missing_measurements
                ):
                    raise DomainError(
                        "ANALYSIS_BIN_MAPPING_REQUIRED",
                        "approved rule rejects Units without one matched Bin Mapping",
                        409,
                    )
                if request.analysis.value == "BIN_COOCCURRENCE":
                    threshold = int(parameters.sparse_matrix_minimum_count)
                    for item, raw_rows, validated, _ in bin_rows_by_work:
                        context = item["context"]
                        unit_lookup = {
                            int(row["unit_id"]): row for row in item["units"]
                        }
                        grouped_units: dict[str, dict[str, set[str]]] = defaultdict(
                            dict
                        )
                        denominators: dict[str, int] = defaultdict(int)
                        for unit_id, rows in validated.items():
                            unit = dict(unit_lookup[unit_id])
                            unit["test_condition"] = None
                            key = _group_key(unit, request.group_by)
                            denominators[key] += 1
                            fail_bins = {
                                f"{row['bin_type']}:{row['raw_bin_code']}"
                                for row in rows
                                if not bool(row["is_pass_snapshot"])
                            }
                            if fail_bins:
                                grouped_units[key][f"UNIT:{unit_id}"] = fail_bins
                        for key, unit_bins in sorted(grouped_units.items()):
                            denominator = denominators[key]
                            for left, right, count, evidence in bin_cooccurrence(
                                unit_bins
                            ):
                                if count < threshold:
                                    continue
                                cooccurrence_results.append(
                                    BinCooccurrenceCell(
                                        int(context["dataset_id"]),
                                        int(context["version_no"]),
                                        key,
                                        left,
                                        right,
                                        count,
                                        denominator,
                                        count / denominator if denominator else 0.0,
                                        evidence,
                                    )
                                )
                else:
                    bin_group_rates: dict[tuple[int, int, str], dict[str, float]] = (
                        defaultdict(dict)
                    )
                    bin_group_details: dict[
                        tuple[int, int, str],
                        dict[str, tuple[int, tuple[int, ...]]],
                    ] = defaultdict(dict)
                    for item, _, validated, _ in bin_rows_by_work:
                        context = item["context"]
                        unit_lookup = {
                            int(row["unit_id"]): row for row in item["units"]
                        }
                        denominator_by_group: dict[str, set[int]] = defaultdict(set)
                        failures: dict[tuple[str, str], set[int]] = defaultdict(set)
                        for unit_id, rows in validated.items():
                            unit = dict(unit_lookup[unit_id])
                            unit["test_condition"] = None
                            group = _group_key(unit, request.group_by)
                            denominator_by_group[group].add(unit_id)
                            for row in rows:
                                if not bool(row["is_pass_snapshot"]):
                                    failures[(group, str(row["raw_bin_code"]))].add(
                                        unit_id
                                    )
                        bins = sorted({bin_code for _, bin_code in failures})
                        for bin_code in bins:
                            for group, denominator in sorted(
                                denominator_by_group.items()
                            ):
                                result_key = (
                                    int(context["dataset_id"]),
                                    int(context["version_no"]),
                                    bin_code,
                                )
                                failed = tuple(sorted(failures[(group, bin_code)]))
                                bin_group_rates[result_key][group] = len(failed) / len(
                                    denominator
                                )
                                bin_group_details[result_key][group] = (
                                    len(denominator),
                                    failed,
                                )
                    for (dataset_id, version_no, bin_code), rates in sorted(
                        bin_group_rates.items()
                    ):
                        group_rows = tuple(
                            SblGroupRate(
                                group,
                                bin_group_details[(dataset_id, version_no, bin_code)][
                                    group
                                ][0],
                                len(
                                    bin_group_details[
                                        (dataset_id, version_no, bin_code)
                                    ][group][1]
                                ),
                                rate,
                                tuple(
                                    f"UNIT:{unit_id}"
                                    for unit_id in bin_group_details[
                                        (dataset_id, version_no, bin_code)
                                    ][group][1]
                                ),
                            )
                            for group, rate in sorted(rates.items())
                        )
                        if len(rates) < parameters.minimum_sample_size:
                            sbl_results.append(
                                SblBinLimit(
                                    dataset_id,
                                    version_no,
                                    bin_code,
                                    len(rates),
                                    None,
                                    None,
                                    None,
                                    "INSUFFICIENT_N",
                                    (),
                                    group_rows,
                                )
                            )
                            continue
                        kernel = sbl_grouped_limit(
                            rates, upper_multiplier=float(parameters.upper_multiplier)
                        )
                        sbl_results.append(
                            SblBinLimit(
                                dataset_id,
                                version_no,
                                bin_code,
                                len(rates),
                                kernel.mean_rate,
                                kernel.sample_stddev,
                                kernel.upper_limit,
                                "ASSESSABLE",
                                kernel.exceeding_groups,
                                group_rows,
                            )
                        )
                    warnings.add(
                        "SBL_GROUPED_LIMIT_V1 returns the unrounded mean plus approved sample-sigma multiplier; it does not copy workbook rounding defaults"
                    )

        if cooccurrence_results:
            ranked_cooccurrence: list[BinCooccurrenceCell] = []
            by_scope: dict[tuple[int, int, str], list[BinCooccurrenceCell]] = (
                defaultdict(list)
            )
            for cell in cooccurrence_results:
                by_scope[(cell.dataset_id, cell.version_no, cell.group_key)].append(
                    cell
                )
            for scope in sorted(by_scope):
                cells = sorted(
                    by_scope[scope],
                    key=lambda item: (
                        -item.physical_unit_count,
                        item.left_bin,
                        item.right_bin,
                    ),
                )
                denominator = sum(item.physical_unit_count for item in cells)
                cumulative = 0
                for rank, cell in enumerate(cells, start=1):
                    cumulative += cell.physical_unit_count
                    ranked_cooccurrence.append(
                        replace(
                            cell,
                            pareto_rank=rank,
                            pair_count_share=(
                                cell.physical_unit_count / denominator
                                if denominator
                                else None
                            ),
                            cumulative_pair_count_share=(
                                cumulative / denominator if denominator else None
                            ),
                        )
                    )
            cooccurrence_results = ranked_cooccurrence

        if sbl_results:
            ranked_sbl = sorted(
                (
                    replace(
                        item,
                        fail_unit_count=sum(
                            group.fail_unit_count for group in item.groups
                        ),
                    )
                    for item in sbl_results
                ),
                key=lambda item: (
                    -item.fail_unit_count,
                    item.dataset_id,
                    item.version_no,
                    item.bin_code,
                ),
            )
            denominator = sum(item.fail_unit_count for item in ranked_sbl)
            cumulative = 0
            sbl_results = []
            for rank, item in enumerate(ranked_sbl, start=1):
                cumulative += item.fail_unit_count
                sbl_results.append(
                    replace(
                        item,
                        pareto_rank=rank,
                        fail_unit_share=(
                            item.fail_unit_count / denominator if denominator else None
                        ),
                        cumulative_fail_unit_share=(
                            cumulative / denominator if denominator else None
                        ),
                    )
                )

        rule_context = AnalyticsRuleContext(
            tuple(
                sorted(
                    selected_spec_versions
                    if request.analysis.value == "MARGIN_OOS"
                    else set(base_rule_context.spec_versions)
                )
            ),
            tuple(
                sorted(
                    selected_bin_versions
                    if request.bin_type is not None
                    else set(base_rule_context.bin_mapping_versions)
                )
            ),
            tuple(
                sorted(
                    {
                        *base_rule_context.evaluation_rule_versions,
                        f"RULE:{rule.rule_code}:{rule.version_code}",
                    }
                )
            ),
        )
        counts = QualityCalculationCounts(
            input_units,
            included_units,
            input_units - included_units,
            included_units if identity is not None else 0,
            included_measurements,
            missing_measurements,
            missing_measurements,
        )
        if request.analysis.value == "PAT_ROBUST_IQR":
            result_count = len(pat_results)
            assessable = any(item.status != "INSUFFICIENT_N" for item in pat_results)
        elif request.analysis.value == "SPC_I_MR":
            result_count = len(spc_results)
            assessable = any(item.status != "INSUFFICIENT_N" for item in spc_results)
        elif request.analysis.value == "MARGIN_OOS":
            result_count = len(margin_results)
            assessable = any(item.points for item in margin_results)
        elif request.analysis.value == "BIN_COOCCURRENCE":
            result_count = len(cooccurrence_results)
            assessable = bool(cooccurrence_results)
        elif request.analysis.value == "SBL_GROUPED_LIMIT":
            result_count = len(sbl_results)
            assessable = any(item.status != "INSUFFICIENT_N" for item in sbl_results)
        elif request.analysis.value == "SYL_GROUPED_LIMIT":
            result_count = len(syl_results)
            assessable = any(item.status != "INSUFFICIENT_N" for item in syl_results)
        else:
            result_count = len(pass_fail_results)
            assessable = any(
                item.status != "INSUFFICIENT_N" for item in pass_fail_results
            )
        if assessable:
            capability = AnalyticsCapability(
                request.analysis.value, "AVAILABLE", None, None
            )
        else:
            reason = (
                "ANALYSIS_INSUFFICIENT_N"
                if result_count
                else "ANALYSIS_NO_INCLUDED_DATA"
            )
            capability = AnalyticsCapability(
                request.analysis.value,
                "UNAVAILABLE",
                reason,
                "approved calculation completed but the selected Context is not assessable",
            )
        point_summaries = (
            tuple(item.sampling_summary for item in spc_results)
            if request.analysis.value == "SPC_I_MR"
            else tuple(item.sampling_summary for item in margin_results)
            if request.analysis.value == "MARGIN_OOS"
            else ()
        )
        if point_summaries:
            original_points = sum(item.original_points for item in point_summaries)
            returned_points = sum(item.returned_points for item in point_summaries)
            preserved_points = sum(
                item.preserved_out_of_spec_points for item in point_summaries
            )
            methods = sorted(
                {item.method for item in point_summaries if item.method is not None}
            )
            sampling_summary = AnalyticsSamplingSummary(
                returned_points < original_points,
                "+".join(methods) if methods else None,
                original_points,
                returned_points,
                preserved_points,
            )
        else:
            sampling_summary = AnalyticsSamplingSummary(
                False, None, included_measurements, included_measurements, 0
            )
        return QualityEvaluationResult(
            _CONTRACT_VERSION,
            request.analysis.value,
            dataset_context,
            filter_summary,
            calculation_hash,
            rule_context,
            rule,
            identity,
            (capability,),
            counts,
            sampling_summary,
            tuple(pat_results),
            tuple(spc_results),
            tuple(margin_results),
            tuple(cooccurrence_results),
            tuple(sbl_results),
            tuple(sorted(warnings)),
            datetime.now(UTC).isoformat(),
            tuple(syl_results),
            tuple(pass_fail_results),
        )
