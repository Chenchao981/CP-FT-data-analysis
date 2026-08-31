from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Protocol

from app.core.errors import DomainError
from app.domain.analytics_risk import (
    QUALITY_RISK_ANALYSES,
    AnalyticsEvaluatedRiskItem,
    AnalyticsInstantRiskRequest,
    AnalyticsInstantRiskResult,
    AnalyticsRiskAnalysis,
    AnalyticsRiskEvaluationConfig,
    AnalyticsRiskRuleProvenance,
)
from app.domain.datasets import (
    DatasetCapabilityConfig,
    DatasetParameterAnalysisFilters,
    DatasetParameterAnalysisRequest,
    DatasetParameterAnalysisResult,
    DatasetParameterAnalysisType,
    DatasetReference,
    DatasetService,
)
from app.domain.quality_evaluation import (
    QualityEvaluationRequest,
    QualityEvaluationResult,
)
from app.infrastructure.sql_analytics_service import _hashes

_CONTRACT_VERSION = "ANALYTICS_INSTANT_RISK_V1"
_MAX_EVIDENCE_KEYS = 100


class QualityEvaluationService(Protocol):
    def analyze(self, request: QualityEvaluationRequest) -> QualityEvaluationResult: ...


def _provenance(
    *,
    rule_code: str,
    version_code: str,
    algorithm_code: str,
    parameters_sha256: str,
) -> AnalyticsRiskRuleProvenance:
    return AnalyticsRiskRuleProvenance(
        rule_code,
        version_code,
        algorithm_code,
        "APPROVED",
        "ENABLED",
        parameters_sha256,
    )


def _bounded_evidence(keys: tuple[str, ...]) -> tuple[tuple[str, ...], bool]:
    unique = tuple(dict.fromkeys(keys))
    return unique[:_MAX_EVIDENCE_KEYS], len(unique) > _MAX_EVIDENCE_KEYS


def _item_code(analysis: str, dataset_id: int, group_key: str) -> str:
    suffix = hashlib.sha256(group_key.encode("utf-8")).hexdigest()[:12].upper()
    return f"{analysis}:{dataset_id}:{suffix}"


def _rate(affected: int, denominator: int) -> float | None:
    return affected / denominator if denominator else None


def _quality_request(
    request: AnalyticsInstantRiskRequest,
    config: AnalyticsRiskEvaluationConfig,
) -> QualityEvaluationRequest:
    parameter = [config.parameter] if config.parameter is not None else []
    if config.group_by is None:
        raise AssertionError("quality risk grouping was not validated")
    return QualityEvaluationRequest(
        datasets=request.datasets,
        filters=request.filters,
        parameters=parameter,
        analysis=QUALITY_RISK_ANALYSES[config.analysis],
        rule=config.rule,
        group_by=config.group_by,
        spc_order=config.spc_order,
        spc_phase=config.spc_phase,
        bin_type=config.bin_type,
    )


class AnalyticsInstantRiskService:
    """Explicit, approved-rule-only reducer over authoritative analysis results."""

    def __init__(
        self,
        datasets: DatasetService,
        quality: QualityEvaluationService,
    ) -> None:
        self._datasets = datasets
        self._quality = quality

    @staticmethod
    def _capability_request(
        request: AnalyticsInstantRiskRequest,
        config: AnalyticsRiskEvaluationConfig,
    ) -> DatasetParameterAnalysisRequest:
        return DatasetParameterAnalysisRequest(
            datasets=[
                DatasetReference(dataset_id=item.dataset_id, version_no=item.version_no)
                for item in request.datasets
            ],
            filters=DatasetParameterAnalysisFilters.model_validate(
                request.filters.model_dump(mode="json")
            ),
            parameters=[config.parameter or ""],
            analyses=[DatasetParameterAnalysisType.CAPABILITY],
            capability=DatasetCapabilityConfig(
                method=config.capability_method,
                rule_code=config.rule.rule_code,
                version_code=config.rule.version_code,
            ),
        )

    @staticmethod
    def _capability_items(
        config: AnalyticsRiskEvaluationConfig,
        result: DatasetParameterAnalysisResult,
    ) -> tuple[AnalyticsEvaluatedRiskItem, ...]:
        built: list[AnalyticsEvaluatedRiskItem] = []
        for group in result.items:
            for parameter_result in group.parameters:
                capability = parameter_result.capability
                if capability is None:
                    raise DomainError(
                        "ANALYSIS_RISK_RESULT_INVALID",
                        "Capability risk requested but the authoritative result is missing",
                        409,
                    )
                if (
                    capability.risk_metric is None
                    or capability.risk_threshold is None
                    or capability.parameters_sha256 is None
                ):
                    raise DomainError(
                        "ANALYSIS_RISK_POLICY_REQUIRED",
                        "approved Capability rule has no explicit risk metric and threshold",
                        409,
                    )
                values = {"CPK": capability.cpk, "PPK": capability.ppk}
                if capability.risk_metric == "MIN_CPK_PPK":
                    metric_value = (
                        min(capability.cpk, capability.ppk)
                        if capability.cpk is not None and capability.ppk is not None
                        else None
                    )
                elif capability.risk_metric in values:
                    metric_value = values[capability.risk_metric]
                else:
                    raise DomainError(
                        "ANALYSIS_RISK_POLICY_INVALID",
                        "approved Capability risk metric is unsupported",
                        409,
                    )
                gated = metric_value is None
                active = (
                    metric_value is not None
                    and metric_value < capability.risk_threshold
                )
                group_key = group.group_key
                if config.capability_method is None:
                    raise AssertionError("Capability method was not validated")
                rule = _provenance(
                    rule_code=config.rule.rule_code,
                    version_code=config.rule.version_code,
                    algorithm_code=config.capability_method.value,
                    parameters_sha256=capability.parameters_sha256,
                )
                built.append(
                    AnalyticsEvaluatedRiskItem(
                        _item_code("CAPABILITY", group.dataset_id, group_key),
                        "CAPABILITY",
                        "CAPABILITY",
                        "WARNING" if active else "INFO",
                        "GATED" if gated else "ACTIVE" if active else "CLEAR",
                        (
                            capability.reason_codes[0]
                            if gated and capability.reason_codes
                            else "ANALYSIS_RISK_METRIC_UNAVAILABLE"
                            if gated
                            else None
                        ),
                        f"{parameter_result.identity.name} Capability",
                        (
                            "Capability 指标不可评估；未执行风险判定。"
                            if gated
                            else f"{capability.risk_metric}={metric_value:.6g}，批准阈值={capability.risk_threshold:.6g}。"
                        ),
                        group.dataset_id,
                        group.version_no,
                        group_key,
                        parameter_result.identity.name,
                        capability.risk_metric,
                        metric_value,
                        "<",
                        capability.risk_threshold,
                        0 if gated or not active else 1,
                        0 if gated else 1,
                        None if gated else (1.0 if active else 0.0),
                        (),
                        False,
                        rule,
                        capability.drilldown_context,
                    )
                )
        return tuple(built)

    @staticmethod
    def _quality_items(
        result: QualityEvaluationResult,
    ) -> tuple[AnalyticsEvaluatedRiskItem, ...]:
        provenance = _provenance(
            rule_code=result.rule.rule_code,
            version_code=result.rule.version_code,
            algorithm_code=result.rule.algorithm_code,
            parameters_sha256=result.rule.parameters_sha256,
        )
        parameter = (
            result.parameter_identity.name if result.parameter_identity else None
        )
        built: list[AnalyticsEvaluatedRiskItem] = []

        def add(
            *,
            dataset_id: int,
            version_no: int,
            group_key: str,
            metric_code: str,
            metric_value: float | None,
            affected: int,
            denominator: int,
            evidence: tuple[str, ...],
            gated: bool,
            reason: str | None,
            title: str,
            message: str,
        ) -> None:
            keys, truncated = _bounded_evidence(evidence)
            active = not gated and affected > 0
            built.append(
                AnalyticsEvaluatedRiskItem(
                    _item_code(result.analysis, dataset_id, group_key),
                    result.analysis,
                    "QUALITY",
                    "WARNING" if active else "INFO",
                    "GATED" if gated else "ACTIVE" if active else "CLEAR",
                    reason,
                    title,
                    message,
                    dataset_id,
                    version_no,
                    group_key,
                    parameter,
                    metric_code,
                    metric_value,
                    "> 0",
                    0.0,
                    affected,
                    denominator,
                    None if gated else _rate(affected, denominator),
                    keys,
                    truncated,
                    provenance,
                )
            )

        for row in result.pat:
            gated = row.status == "INSUFFICIENT_N"
            add(
                dataset_id=row.dataset_id,
                version_no=row.version_no,
                group_key=row.group_key,
                metric_code="PAT_OUTLIER_RATE",
                metric_value=row.outlier_rate,
                affected=row.outlier_count,
                denominator=row.valid_n,
                evidence=tuple(item.drilldown_key for item in row.evidence),
                gated=gated,
                reason="ANALYSIS_INSUFFICIENT_N" if gated else None,
                title=f"PAT · {row.group_key}",
                message="按批准 PAT limit 和服务端 evidence 汇总异常点。",
            )
        for row in result.spc:
            signals = tuple(
                point.drilldown_key
                for point in row.points
                if point.rule_hits
                or (
                    row.lower_control_limit is not None
                    and point.value < row.lower_control_limit
                )
                or (
                    row.upper_control_limit is not None
                    and point.value > row.upper_control_limit
                )
            )
            gated = row.status == "INSUFFICIENT_N"
            add(
                dataset_id=row.dataset_id,
                version_no=row.version_no,
                group_key=row.group_key,
                metric_code="SPC_SIGNAL_RATE",
                metric_value=_rate(len(signals), row.valid_n),
                affected=len(signals),
                denominator=row.valid_n,
                evidence=signals,
                gated=gated,
                reason="ANALYSIS_INSUFFICIENT_N" if gated else None,
                title=f"SPC I-MR · {row.group_key}",
                message="按服务端 control limits 与批准 run-rule hits 汇总信号。",
            )
        for row in result.margin:
            evidence = tuple(
                point.drilldown_key for point in row.points if point.out_of_spec
            )
            add(
                dataset_id=row.dataset_id,
                version_no=row.version_no,
                group_key=row.group_key,
                metric_code="MARGIN_OOS_RATE",
                metric_value=row.out_of_spec_rate,
                affected=row.out_of_spec_count,
                denominator=row.valid_n,
                evidence=evidence,
                gated=not bool(row.points),
                reason="ANALYSIS_NO_INCLUDED_DATA" if not row.points else None,
                title=f"Spec Margin · {row.group_key}",
                message="按服务端正式 Spec 与批准边界语义汇总 OOS。",
            )
        for row in result.sbl:
            evidence = tuple(
                key
                for group in row.groups
                if group.group_key in row.exceeding_groups
                for key in group.drilldown_keys
            )
            gated = row.status == "INSUFFICIENT_N"
            add(
                dataset_id=row.dataset_id,
                version_no=row.version_no,
                group_key=f"BIN:{row.bin_code}",
                metric_code="SBL_EXCEEDING_GROUP_RATE",
                metric_value=_rate(len(row.exceeding_groups), row.subgroup_count),
                affected=len(row.exceeding_groups),
                denominator=row.subgroup_count,
                evidence=evidence,
                gated=gated,
                reason="ANALYSIS_INSUFFICIENT_N" if gated else None,
                title=f"SBL · Bin {row.bin_code}",
                message="按服务端批准 SBL upper limit 汇总超限子组。",
            )
        for row in result.syl:
            evidence = tuple(
                key
                for group in row.groups
                if group.group_key in row.below_limit_groups
                for key in group.drilldown_keys
            )
            gated = row.status == "INSUFFICIENT_N"
            add(
                dataset_id=row.dataset_id,
                version_no=row.version_no,
                group_key=f"DATASET:{row.dataset_id}",
                metric_code="SYL_BELOW_GROUP_RATE",
                metric_value=_rate(len(row.below_limit_groups), row.subgroup_count),
                affected=len(row.below_limit_groups),
                denominator=row.subgroup_count,
                evidence=evidence,
                gated=gated,
                reason="ANALYSIS_INSUFFICIENT_N" if gated else None,
                title=f"SYL · Dataset #{row.dataset_id}",
                message="按服务端批准 SYL lower limit 与 rounding policy 汇总低良率子组。",
            )
        return tuple(built)

    def evaluate(
        self, request: AnalyticsInstantRiskRequest
    ) -> AnalyticsInstantRiskResult:
        summary = _hashes(request)
        items: list[AnalyticsEvaluatedRiskItem] = []
        calculation_hashes: list[str] = []
        warnings: set[str] = set()
        for config in request.evaluations:
            if config.analysis == AnalyticsRiskAnalysis.CAPABILITY:
                result = self._datasets.analyze_parameters(
                    self._capability_request(request, config)
                )
                requested_datasets = {
                    (item.dataset_id, item.version_no) for item in request.datasets
                }
                resolved_datasets = {
                    (item.dataset_id, item.version_no)
                    for item in result.dataset_context.resolved_datasets
                }
                if (
                    result.filter_summary.filter_hash != summary.filter_hash
                    or resolved_datasets != requested_datasets
                ):
                    raise DomainError(
                        "ANALYSIS_CONTEXT_MISMATCH",
                        "Capability risk result does not match the requested Context",
                        409,
                    )
                capability_items = self._capability_items(config, result)
                items.extend(capability_items)
                if config.capability_method is None:
                    raise AssertionError("Capability method was not validated")
                calculation_hashes.append(
                    hashlib.sha256(
                        json.dumps(
                            {
                                "context": summary.context_hash,
                                "analysis": config.analysis.value,
                                "rule": config.rule.model_dump(mode="json"),
                                "method": config.capability_method.value,
                                "parameter": config.parameter,
                                "parameters_sha256": sorted(
                                    {
                                        item.rule.parameters_sha256
                                        for item in capability_items
                                    }
                                ),
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                )
                warnings.update(result.warnings)
            else:
                result = self._quality.analyze(_quality_request(request, config))
                requested_datasets = {
                    (item.dataset_id, item.version_no) for item in request.datasets
                }
                resolved_datasets = {
                    (item.dataset_id, item.version_no)
                    for item in result.dataset_context.resolved_datasets
                }
                if (
                    result.filter_summary.filter_hash != summary.filter_hash
                    or resolved_datasets != requested_datasets
                ):
                    raise DomainError(
                        "ANALYSIS_CONTEXT_MISMATCH",
                        "quality risk result does not match the requested Context",
                        409,
                    )
                items.extend(self._quality_items(result))
                calculation_hashes.append(result.calculation_context_hash)
                warnings.update(result.warnings)
        calculation_context_hash = hashlib.sha256(
            json.dumps(
                {
                    "context_hash": summary.context_hash,
                    "calculations": calculation_hashes,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return AnalyticsInstantRiskResult(
            _CONTRACT_VERSION,
            summary,
            calculation_context_hash,
            tuple(config.analysis.value for config in request.evaluations),
            tuple(items),
            tuple(sorted(warnings)),
            datetime.now(UTC).isoformat(),
        )
