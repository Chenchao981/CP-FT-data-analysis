from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import asdict
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, Engine

from app.core.errors import DomainError
from app.domain.analytics import AnalyticsDetailRequest, AnalyticsOverviewRequest
from app.domain.analytics_export_analysis import (
    AnalyticsExportAnalysisConfig,
    resolve_analytics_export_analysis_config,
)
from app.domain.analytics_export_worker import (
    AnalyticsExportTable,
    AnalyticsExportWorkItem,
    ExportCell,
)
from app.domain.analytics_exports import resolve_current_page_detail_state
from app.domain.analytics_risk import AnalyticsInstantRiskRequest
from app.domain.datasets import DatasetParameterAnalysisRequest
from app.domain.parameter_relationship import ParameterRelationshipRequest
from app.domain.quality_evaluation import QualityEvaluationRequest
from app.domain.saved_analyses import canonical_json
from app.domain.spatial_analysis import SpatialAnalysisRequest
from app.domain.wafer_summary import WaferSummaryRequest
from app.infrastructure.analytics_instant_risk_service import (
    AnalyticsInstantRiskService,
)
from app.infrastructure.sql_analysis_rule_service import SqlAnalysisRuleService
from app.infrastructure.sql_analytics_service import (
    SqlAnalyticsService,
    _condition_text,
    _detail_order_sql,
    _finite_float,
    _source_identity,
)
from app.infrastructure.sql_dataset_service import SqlDatasetService
from app.infrastructure.sql_parameter_relationship_service import (
    SqlParameterRelationshipService,
)
from app.infrastructure.sql_quality_evaluation_service import (
    SqlQualityEvaluationService,
)
from app.infrastructure.sql_spatial_analysis_service import SqlSpatialAnalysisService
from app.infrastructure.sql_wafer_summary_service import SqlWaferSummaryService

_UNIT_COLUMNS = (
    "dataset_id",
    "version_no",
    "test_stage",
    "unit_id",
    "drilldown_key",
    "logical_unit_key",
    "lot_id",
    "wafer_id",
    "x",
    "y",
    "soft_bin",
    "hard_bin",
    "overall_result",
    "source_row_no",
    "source_id",
    "tester_id",
    "program_version",
)

_ANALYSIS_REPORT_COLUMNS = (
    "record_type",
    "dataset_id",
    "version_no",
    "group_key",
    "parameter",
    "secondary_parameter",
    "sequence",
    "status",
    "metric_value",
    "drilldown_key",
    "details_json",
)


def _statement(sql: str, expanding: tuple[str, ...] = ()):
    statement = text(sql)
    if expanding:
        statement = statement.bindparams(
            *(bindparam(name, expanding=True) for name in expanding)
        )
    return statement


class SqlAnalyticsExportContentSource:
    """Read the immutable Canonical chain for the exact queued Dataset Versions."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def table(self, work_item: AnalyticsExportWorkItem) -> AnalyticsExportTable:
        if work_item.export_scope.value == "REPORT":
            try:
                config = resolve_analytics_export_analysis_config(
                    work_item.template_code, work_item.chart_config
                )
            except ValueError as exc:
                raise DomainError(
                    "ANALYTICS_EXPORT_ANALYSIS_CONFIG_INVALID",
                    "the queued report does not contain a valid exact analysis request",
                    409,
                ) from exc
            if config is None:
                raise DomainError(
                    "ANALYTICS_EXPORT_TEMPLATE_RENDERER_UNAVAILABLE",
                    "the requested analytics report does not yet have a server renderer",
                    409,
                )
            return AnalyticsExportTable(
                columns=_ANALYSIS_REPORT_COLUMNS,
                rows=self._iter_analysis_report_rows(work_item, config),
            )
        if work_item.template_code not in {"ANALYTICS_DETAIL", "PARAMETER_DETAIL"}:
            raise DomainError(
                "ANALYTICS_EXPORT_TEMPLATE_RENDERER_UNAVAILABLE",
                "the requested analytics data template does not have a server renderer",
                409,
            )
        if (
            work_item.template_code == "PARAMETER_DETAIL"
            and not work_item.context.parameters
        ):
            raise DomainError(
                "ANALYTICS_EXPORT_PARAMETER_REQUIRED",
                "PARAMETER_DETAIL requires at least one exact parameter identity",
                409,
            )
        parameter_columns = tuple(
            column
            for parameter in work_item.context.parameters
            for column in (
                parameter,
                f"{parameter}__measurement_status",
                f"{parameter}__unit",
                f"{parameter}__program_lsl",
                f"{parameter}__program_usl",
            )
        )
        return AnalyticsExportTable(
            columns=_UNIT_COLUMNS + parameter_columns,
            rows=self._iter_detail_rows(work_item),
        )

    @staticmethod
    def _details(value: Any, *, exclude: tuple[str, ...] = ()) -> str:
        payload = asdict(value) if not isinstance(value, dict) else dict(value)
        for field in exclude:
            payload.pop(field, None)
        rendered = canonical_json(payload)
        if len(rendered) > 30_000:
            raise DomainError(
                "ANALYTICS_EXPORT_CONTENT_CELL_LIMIT",
                "one normalized analysis record exceeds the portable report cell limit",
                409,
            )
        return rendered

    @classmethod
    def _analysis_row(
        cls,
        record_type: str,
        value: Any,
        *,
        dataset_id: int | None = None,
        version_no: int | None = None,
        group_key: str | None = None,
        parameter: str | None = None,
        secondary_parameter: str | None = None,
        sequence: int | None = None,
        status: str | None = None,
        metric_value: ExportCell = None,
        drilldown_key: str | None = None,
        exclude: tuple[str, ...] = (),
    ) -> tuple[ExportCell, ...]:
        return (
            record_type,
            dataset_id,
            version_no,
            group_key,
            parameter,
            secondary_parameter,
            sequence,
            status,
            metric_value,
            drilldown_key,
            cls._details(value, exclude=exclude),
        )

    @staticmethod
    def _rule_identity(value: str) -> str:
        parts = value.split(":")
        if len(parts) >= 3 and parts[0] == "RULE":
            return ":".join(parts[:3])
        return value

    @classmethod
    def _assert_rule_context(cls, work_item: AnalyticsExportWorkItem, result) -> None:
        result_context = result.rule_context
        stored_spec = set(work_item.rule_context.spec_versions)
        stored_bin = set(work_item.rule_context.bin_mapping_versions)
        stored_rules = {
            cls._rule_identity(item)
            for item in work_item.rule_context.evaluation_rule_versions
        }
        required_rules = {
            cls._rule_identity(item) for item in result_context.evaluation_rule_versions
        }
        stored_spec_ids = {
            item.split(":", 2)[1]
            for item in stored_spec
            if item.startswith("SPEC:") and len(item.split(":", 2)) == 3
        }
        missing_specs = sorted(
            item
            for item in set(result_context.spec_versions)
            if item not in stored_spec
            and not (
                item.startswith("SPEC_SET:")
                and item.split(":", 1)[1] in stored_spec_ids
            )
        )
        missing_bins = sorted(set(result_context.bin_mapping_versions) - stored_bin)
        missing_rules = sorted(required_rules - stored_rules)
        if missing_specs or missing_bins or missing_rules:
            raise DomainError(
                "ANALYTICS_EXPORT_RULE_CONTEXT_STALE",
                "the rendered analysis rule context differs from the queued context",
                409,
                details=[
                    {
                        "missing_spec_versions": missing_specs,
                        "missing_bin_mapping_versions": missing_bins,
                        "missing_evaluation_rule_versions": missing_rules,
                    }
                ],
            )

    @classmethod
    def _result_context_rows(
        cls,
        work_item: AnalyticsExportWorkItem,
        result,
        *,
        extras: Mapping[str, ExportCell] | None = None,
    ) -> Iterator[tuple[ExportCell, ...]]:
        cls._assert_rule_context(work_item, result)
        yield cls._analysis_row(
            "RESULT_CONTRACT",
            {
                "contract_version": result.contract_version,
                "computed_at": result.computed_at,
                "filter_hash": work_item.filter_hash,
                "context_hash": work_item.context_hash,
                "presentation_hash": work_item.presentation_hash,
            },
        )
        for dataset in result.dataset_context.resolved_datasets:
            yield cls._analysis_row(
                "DATASET_CONTEXT",
                dataset,
                dataset_id=dataset.dataset_id,
                version_no=dataset.version_no,
                status=(
                    "CURRENT_PUBLISHED_VERIFIED"
                    if result.dataset_context.current_published_verified
                    else "UNVERIFIED"
                ),
            )
        normalized_filters = asdict(result.filter_summary.normalized_filters)
        for name, values in normalized_filters.items():
            yield cls._analysis_row(
                "FILTER_CONTEXT",
                {"name": name, "values": values},
                status="APPLIED",
                metric_value=len(values),
            )
        for name in (
            "spec_versions",
            "bin_mapping_versions",
            "evaluation_rule_versions",
        ):
            for identity in getattr(result.rule_context, name):
                yield cls._analysis_row(
                    "RULE_CONTEXT",
                    {"category": name, "identity": identity},
                    status="PINNED",
                )
        for capability in result.capabilities:
            yield cls._analysis_row(
                "CAPABILITY",
                capability,
                status=capability.status,
                metric_value=capability.code,
            )
        if hasattr(result, "counts"):
            yield cls._analysis_row("COUNTS", result.counts)
        if hasattr(result, "sampling_summary"):
            yield cls._analysis_row("SAMPLING", result.sampling_summary)
        for warning in result.warnings:
            yield cls._analysis_row("WARNING", {"message": warning}, status="WARNING")
        for name, value in (extras or {}).items():
            yield cls._analysis_row("ANALYSIS_CONTEXT", {"name": name, "value": value})

    @staticmethod
    def _context_request_payload(work_item: AnalyticsExportWorkItem) -> dict[str, Any]:
        return {
            "datasets": [
                item.model_dump(mode="json") for item in work_item.context.datasets
            ],
            "filters": work_item.context.filters.model_dump(mode="json"),
        }

    def _overview_report_rows(
        self,
        work_item: AnalyticsExportWorkItem,
        config: AnalyticsExportAnalysisConfig,
    ) -> Iterator[tuple[ExportCell, ...]]:
        focus = work_item.display_config.get("focus_dataset_id")
        selected_ids = {item.dataset_id for item in work_item.context.datasets}
        focus_dataset_id = (
            int(focus)
            if isinstance(focus, int)
            and not isinstance(focus, bool)
            and focus in selected_ids
            else work_item.context.datasets[0].dataset_id
        )
        result = SqlAnalyticsService(self._engine).overview(
            AnalyticsOverviewRequest(
                **work_item.context.model_dump(mode="json"),
                focus_dataset_id=focus_dataset_id,
                max_points=20_000,
            )
        )
        yield from self._result_context_rows(
            work_item, result, extras={"focus_dataset_id": focus_dataset_id}
        )
        for item in result.datasets:
            yield self._analysis_row(
                "DATASET_OVERVIEW",
                item,
                dataset_id=item.dataset_id,
                version_no=item.version_no,
                metric_value=item.yield_rate,
            )
        for item in result.yield_trend:
            yield self._analysis_row(
                "YIELD_TREND",
                item,
                dataset_id=item.dataset_id,
                version_no=item.version_no,
                group_key=f"RUN:{item.run_id}",
                sequence=item.sequence,
                metric_value=item.yield_rate,
                drilldown_key=item.drilldown_key,
            )
        for item in result.bin_pareto:
            yield self._analysis_row(
                "BIN_PARETO",
                item,
                dataset_id=item.dataset_id,
                version_no=item.version_no,
                group_key=f"{item.bin_type}:{item.bin_code}",
                status="PASS" if item.is_pass else "FAIL",
                metric_value=item.percent,
                drilldown_key=item.drilldown_key,
            )
        for item in result.wafer_map:
            yield self._analysis_row(
                "WAFER_MAP_POINT",
                item,
                group_key=f"X:{item.x}:Y:{item.y}",
                status=item.result,
                metric_value=item.bin_code,
                drilldown_key=item.drilldown_key,
            )
        for item in result.risk_summary:
            yield self._analysis_row(
                "RISK_SUMMARY",
                item,
                group_key=item.category,
                status=item.status,
                metric_value=item.rate,
            )
        overview_config = config.overview
        assert overview_config is not None
        if not overview_config.evaluations:
            yield self._analysis_row(
                "INSTANT_RISK_CONTEXT",
                {
                    "status": "NOT_REQUESTED",
                    "reason_code": "ANALYTICS_INSTANT_RISK_NOT_SELECTED",
                },
                status="NOT_REQUESTED",
            )
            return
        instant = AnalyticsInstantRiskService(
            SqlDatasetService(self._engine),
            SqlQualityEvaluationService(self._engine),
        ).evaluate(
            AnalyticsInstantRiskRequest(
                **work_item.context.model_dump(mode="json"),
                evaluations=overview_config.evaluations,
            )
        )
        if (
            instant.filter_summary.filter_hash != work_item.filter_hash
            or instant.filter_summary.context_hash != work_item.context_hash
        ):
            raise DomainError(
                "ANALYTICS_EXPORT_CONTEXT_STALE",
                "the instant-risk result does not match the queued export context",
                409,
            )
        stored_rules = {
            self._rule_identity(item)
            for item in work_item.rule_context.evaluation_rule_versions
        }
        required_rules = {
            f"RULE:{item.rule.rule_code}:{item.rule.version_code}"
            for item in instant.items
        }
        missing_rules = sorted(required_rules - stored_rules)
        if missing_rules:
            raise DomainError(
                "ANALYTICS_EXPORT_RULE_CONTEXT_STALE",
                "the instant-risk rules differ from the queued export context",
                409,
                details=[{"missing_evaluation_rule_versions": missing_rules}],
            )
        yield self._analysis_row(
            "INSTANT_RISK_CONTEXT",
            {
                "contract_version": instant.contract_version,
                "calculation_context_hash": instant.calculation_context_hash,
                "requested_analyses": instant.requested_analyses,
                "computed_at": instant.computed_at,
            },
            status="EVALUATED",
            metric_value=len(instant.items),
        )
        for warning in instant.warnings:
            yield self._analysis_row(
                "INSTANT_RISK_WARNING", {"message": warning}, status="WARNING"
            )
        for item in instant.items:
            base = {
                "dataset_id": item.dataset_id,
                "version_no": item.version_no,
                "group_key": item.group_key,
                "parameter": item.parameter,
            }
            yield self._analysis_row(
                "INSTANT_RISK",
                item,
                exclude=("evidence_drilldown_keys",),
                status=item.status,
                metric_value=item.metric_value,
                **base,
            )
            for key in item.evidence_drilldown_keys:
                yield self._analysis_row(
                    "INSTANT_RISK_EVIDENCE",
                    {"drilldown_key": key, "risk_code": item.code},
                    status=item.status,
                    drilldown_key=key,
                    **base,
                )

    def _parameter_report_rows(
        self,
        work_item: AnalyticsExportWorkItem,
        config: AnalyticsExportAnalysisConfig,
    ) -> Iterator[tuple[ExportCell, ...]]:
        selected = config.parameter_analysis
        assert selected is not None
        result = SqlDatasetService(self._engine).analyze_parameters(
            DatasetParameterAnalysisRequest(
                **self._context_request_payload(work_item),
                parameters=selected.parameters,
                group_by=selected.group_by,
                analyses=selected.analyses,
                box_plot=selected.box_plot,
                histogram=selected.histogram,
                normal_fit=selected.normal_fit,
                capability=selected.capability,
            )
        )
        yield from self._result_context_rows(
            work_item, result, extras={"group_by": result.group_by}
        )
        for item in result.items:
            for parameter_result in item.parameters:
                parameter = parameter_result.identity.name
                base = {
                    "dataset_id": item.dataset_id,
                    "version_no": item.version_no,
                    "group_key": item.group_key,
                    "parameter": parameter,
                }
                yield self._analysis_row(
                    "PARAMETER_IDENTITY", parameter_result.identity, **base
                )
                for status_count in parameter_result.status_counts:
                    yield self._analysis_row(
                        "MEASUREMENT_STATUS",
                        status_count,
                        status=status_count.status,
                        metric_value=status_count.count,
                        **base,
                    )
                if parameter_result.descriptive is not None:
                    yield self._analysis_row(
                        "DESCRIPTIVE", parameter_result.descriptive, **base
                    )
                if parameter_result.box_plot is not None:
                    box = parameter_result.box_plot
                    yield self._analysis_row(
                        "BOX_PLOT",
                        box,
                        exclude=("outlier_evidence", "outlier_sampling"),
                        status="AVAILABLE",
                        metric_value=box.outlier_count,
                        **base,
                    )
                    for evidence in box.outlier_evidence:
                        yield self._analysis_row(
                            "BOX_OUTLIER",
                            evidence,
                            metric_value=evidence.value,
                            status=evidence.spec_status,
                            drilldown_key=evidence.drilldown_key,
                            **base,
                        )
                    if box.outlier_sampling is not None:
                        yield self._analysis_row(
                            "BOX_OUTLIER_SAMPLING", box.outlier_sampling, **base
                        )
                if parameter_result.histogram is not None:
                    histogram = parameter_result.histogram
                    yield self._analysis_row(
                        "HISTOGRAM",
                        histogram,
                        exclude=("bins",),
                        metric_value=histogram.bin_count,
                        **base,
                    )
                    for bin_item in histogram.bins:
                        yield self._analysis_row(
                            "HISTOGRAM_BIN",
                            bin_item,
                            sequence=bin_item.index,
                            status=bin_item.spec_region,
                            metric_value=bin_item.count,
                            **base,
                        )
                if parameter_result.normal_fit is not None:
                    normal = parameter_result.normal_fit
                    yield self._analysis_row(
                        "NORMAL_FIT",
                        normal,
                        exclude=("points", "observed_evidence", "evidence_sampling"),
                        status=normal.status,
                        metric_value=normal.sample_count,
                        **base,
                    )
                    for sequence, point in enumerate(normal.points, start=1):
                        yield self._analysis_row(
                            "NORMAL_FIT_POINT",
                            point,
                            sequence=sequence,
                            metric_value=point.probability_density,
                            **base,
                        )
                    for evidence in normal.observed_evidence:
                        yield self._analysis_row(
                            "NORMAL_FIT_EVIDENCE",
                            evidence,
                            metric_value=evidence.value,
                            status=evidence.spec_status,
                            drilldown_key=evidence.drilldown_key,
                            **base,
                        )
                    if normal.evidence_sampling is not None:
                        yield self._analysis_row(
                            "NORMAL_FIT_SAMPLING", normal.evidence_sampling, **base
                        )
                if parameter_result.capability is not None:
                    capability = parameter_result.capability
                    yield self._analysis_row(
                        "CAPABILITY",
                        capability,
                        status=capability.status,
                        metric_value=capability.cpk,
                        **base,
                    )

    def _relationship_report_rows(
        self,
        work_item: AnalyticsExportWorkItem,
        config: AnalyticsExportAnalysisConfig,
    ) -> Iterator[tuple[ExportCell, ...]]:
        selected = config.parameter_relationship
        assert selected is not None
        result = SqlParameterRelationshipService(self._engine).relationship(
            ParameterRelationshipRequest(
                **self._context_request_payload(work_item),
                x_parameter=selected.x_parameter,
                y_parameters=selected.y_parameters,
                analyses=selected.analyses,
                group_by=selected.group_by,
                max_points=selected.max_points,
                correlation=selected.correlation,
            )
        )
        yield from self._result_context_rows(
            work_item,
            result,
            extras={
                "group_by": result.group_by,
                "trend_order_basis": result.trend_order_basis,
            },
        )
        for item in result.items:
            base = {
                "dataset_id": item.dataset_id,
                "version_no": item.version_no,
                "group_key": item.group_key,
            }
            for identity in item.identities:
                yield self._analysis_row(
                    "PARAMETER_IDENTITY", identity, parameter=identity.name, **base
                )
            for point in item.scatter_points:
                yield self._analysis_row(
                    "SCATTER_POINT",
                    point,
                    parameter=point.x_parameter,
                    secondary_parameter=point.y_parameter,
                    status=(
                        "OUT_OF_SPEC"
                        if point.x_out_of_spec or point.y_out_of_spec
                        else "IN_SPEC"
                    ),
                    metric_value=point.y_value,
                    drilldown_key=point.drilldown_key,
                    **base,
                )
            for point in item.trend_points:
                yield self._analysis_row(
                    "TREND_POINT",
                    point,
                    parameter=point.parameter,
                    sequence=point.ordinal,
                    status="OUT_OF_SPEC" if point.out_of_spec else "IN_SPEC",
                    metric_value=point.value,
                    drilldown_key=point.drilldown_key,
                    **base,
                )
            for correlation in item.correlations:
                yield self._analysis_row(
                    "CORRELATION",
                    correlation,
                    parameter=correlation.x_parameter,
                    secondary_parameter=correlation.y_parameter,
                    status=correlation.status,
                    metric_value=correlation.coefficient,
                    **base,
                )

    def _spatial_report_rows(
        self,
        work_item: AnalyticsExportWorkItem,
        config: AnalyticsExportAnalysisConfig,
    ) -> Iterator[tuple[ExportCell, ...]]:
        selected = config.spatial_analysis
        assert selected is not None
        result = SqlSpatialAnalysisService(
            self._engine, rule_service=SqlAnalysisRuleService(self._engine)
        ).analyze(
            SpatialAnalysisRequest(
                **self._context_request_payload(work_item),
                parameters=[selected.parameter]
                if selected.parameter is not None
                else [],
                mode=selected.mode,
                focus_dataset_id=selected.focus_dataset_id,
                max_points=selected.max_points,
                rule_code=selected.rule_code,
                rule_version=selected.rule_version,
            )
        )
        yield from self._result_context_rows(
            work_item,
            result,
            extras={"mode": result.mode, "parameter": result.parameter},
        )
        yield self._analysis_row("SPATIAL_DATA_QUALITY", result.data_quality)
        if result.color_domain is not None:
            yield self._analysis_row("SPATIAL_COLOR_DOMAIN", result.color_domain)
        if result.zone_geometry is not None:
            yield self._analysis_row("SPATIAL_ZONE_GEOMETRY", result.zone_geometry)
        for wafer in result.wafer_manifest:
            yield self._analysis_row(
                "WAFER_MANIFEST",
                wafer,
                dataset_id=wafer.dataset_id,
                version_no=wafer.version_no,
                group_key=wafer.key,
            )
        for record_type, points in (
            ("SPATIAL_POINT", result.points),
            ("SPATIAL_WAFER_LAYER", result.wafer_layers),
        ):
            for point in points:
                yield self._analysis_row(
                    record_type,
                    point,
                    dataset_id=point.dataset_id,
                    version_no=point.version_no,
                    group_key=(
                        f"{point.lot_id}|{point.wafer_id}|X:{point.x}|Y:{point.y}"
                    ),
                    parameter=result.parameter,
                    status=point.spec_status or point.result,
                    metric_value=point.value,
                    drilldown_key=point.drilldown_key,
                )
        for zone in result.zones:
            yield self._analysis_row(
                "SPATIAL_ZONE",
                zone,
                group_key=zone.zone,
                parameter=result.parameter,
                metric_value=zone.yield_rate,
                drilldown_key=zone.drilldown_key,
            )
        for quadrant in result.quadrants:
            yield self._analysis_row(
                "SPATIAL_QUADRANT",
                quadrant,
                group_key=quadrant.quadrant,
                parameter=result.parameter,
                metric_value=quadrant.yield_rate,
            )

    def _quality_report_rows(
        self,
        work_item: AnalyticsExportWorkItem,
        config: AnalyticsExportAnalysisConfig,
    ) -> Iterator[tuple[ExportCell, ...]]:
        selected = config.ft_quality
        assert selected is not None
        result = SqlQualityEvaluationService(self._engine).analyze(
            QualityEvaluationRequest(
                **self._context_request_payload(work_item),
                parameters=[selected.parameter]
                if selected.parameter is not None
                else [],
                analysis=selected.analysis,
                rule=selected.rule,
                group_by=selected.group_by,
                spc_order=selected.spc_order,
                spc_phase=selected.spc_phase,
                bin_type=selected.bin_type,
            )
        )
        yield from self._result_context_rows(
            work_item,
            result,
            extras={
                "analysis": result.analysis,
                "calculation_context_hash": result.calculation_context_hash,
            },
        )
        yield self._analysis_row("QUALITY_RULE", result.rule, status="APPROVED")
        if result.parameter_identity is not None:
            yield self._analysis_row(
                "QUALITY_PARAMETER_IDENTITY",
                result.parameter_identity,
                parameter=result.parameter_identity.name,
            )
        parameter = selected.parameter
        for group in result.pat:
            base = {
                "dataset_id": group.dataset_id,
                "version_no": group.version_no,
                "group_key": group.group_key,
                "parameter": parameter,
            }
            yield self._analysis_row(
                "PAT_GROUP",
                group,
                exclude=("evidence",),
                status=group.status,
                metric_value=group.outlier_rate,
                **base,
            )
            for evidence in group.evidence:
                yield self._analysis_row(
                    "PAT_EVIDENCE",
                    evidence,
                    status=evidence.reason_code,
                    metric_value=evidence.value,
                    drilldown_key=evidence.drilldown_key,
                    **base,
                )
        for group in result.spc:
            base = {
                "dataset_id": group.dataset_id,
                "version_no": group.version_no,
                "group_key": group.group_key,
                "parameter": parameter,
            }
            yield self._analysis_row(
                "SPC_GROUP",
                group,
                exclude=("points",),
                status=group.status,
                metric_value=group.center_line,
                **base,
            )
            for point in group.points:
                yield self._analysis_row(
                    "SPC_POINT",
                    point,
                    sequence=point.sequence,
                    status="RULE_HIT" if point.rule_hits else "IN_CONTROL",
                    metric_value=point.value,
                    drilldown_key=point.drilldown_key,
                    **base,
                )
        for group in result.margin:
            base = {
                "dataset_id": group.dataset_id,
                "version_no": group.version_no,
                "group_key": group.group_key,
                "parameter": parameter,
            }
            yield self._analysis_row(
                "MARGIN_GROUP",
                group,
                exclude=("points",),
                metric_value=group.out_of_spec_rate,
                **base,
            )
            for point in group.points:
                yield self._analysis_row(
                    "MARGIN_POINT",
                    point,
                    status="OUT_OF_SPEC" if point.out_of_spec else "IN_SPEC",
                    metric_value=point.nearest_margin,
                    drilldown_key=point.drilldown_key,
                    **base,
                )
        for cell in result.bin_cooccurrence:
            base = {
                "dataset_id": cell.dataset_id,
                "version_no": cell.version_no,
                "group_key": cell.group_key,
                "parameter": cell.left_bin,
                "secondary_parameter": cell.right_bin,
            }
            yield self._analysis_row(
                "BIN_COOCCURRENCE",
                cell,
                exclude=("drilldown_keys",),
                metric_value=cell.rate,
                **base,
            )
            for key in cell.drilldown_keys:
                yield self._analysis_row(
                    "BIN_COOCCURRENCE_EVIDENCE",
                    {"drilldown_key": key},
                    drilldown_key=key,
                    **base,
                )
        for limit in result.sbl:
            base = {
                "dataset_id": limit.dataset_id,
                "version_no": limit.version_no,
                "parameter": limit.bin_code,
            }
            yield self._analysis_row(
                "SBL_LIMIT",
                limit,
                exclude=("groups",),
                status=limit.status,
                metric_value=limit.upper_limit,
                **base,
            )
            for group in limit.groups:
                yield self._analysis_row(
                    "SBL_GROUP",
                    group,
                    exclude=("drilldown_keys",),
                    group_key=group.group_key,
                    metric_value=group.rate,
                    **base,
                )
                for key in group.drilldown_keys:
                    yield self._analysis_row(
                        "SBL_EVIDENCE",
                        {"drilldown_key": key},
                        group_key=group.group_key,
                        drilldown_key=key,
                        **base,
                    )
        for limit in result.syl:
            base = {
                "dataset_id": limit.dataset_id,
                "version_no": limit.version_no,
            }
            yield self._analysis_row(
                "SYL_LIMIT",
                limit,
                exclude=("groups",),
                status=limit.status,
                metric_value=limit.lower_limit,
                **base,
            )
            for group in limit.groups:
                yield self._analysis_row(
                    "SYL_GROUP",
                    group,
                    exclude=("drilldown_keys",),
                    group_key=group.group_key,
                    metric_value=group.yield_rate,
                    **base,
                )
                for key in group.drilldown_keys:
                    yield self._analysis_row(
                        "SYL_EVIDENCE",
                        {"drilldown_key": key},
                        group_key=group.group_key,
                        drilldown_key=key,
                        **base,
                    )
        for group in result.pass_fail_distribution:
            base = {
                "dataset_id": group.dataset_id,
                "version_no": group.version_no,
                "group_key": group.group_key,
                "parameter": parameter,
            }
            yield self._analysis_row(
                "PASS_FAIL_DISTRIBUTION",
                group,
                exclude=("bins",),
                status=group.status,
                metric_value=group.fail_count,
                **base,
            )
            for bin_item in group.bins:
                yield self._analysis_row(
                    "PASS_FAIL_BIN",
                    bin_item,
                    exclude=("pass_drilldown_keys", "fail_drilldown_keys"),
                    sequence=bin_item.bin_index,
                    metric_value=bin_item.pass_count + bin_item.fail_count,
                    **base,
                )
                for status, keys in (
                    ("PASS", bin_item.pass_drilldown_keys),
                    ("FAIL", bin_item.fail_drilldown_keys),
                ):
                    for key in keys:
                        yield self._analysis_row(
                            "PASS_FAIL_EVIDENCE",
                            {"drilldown_key": key},
                            sequence=bin_item.bin_index,
                            status=status,
                            drilldown_key=key,
                            **base,
                        )

    def _wafer_summary_report_rows(
        self,
        work_item: AnalyticsExportWorkItem,
        config: AnalyticsExportAnalysisConfig,
    ) -> Iterator[tuple[ExportCell, ...]]:
        selected = config.wafer_summary
        assert selected is not None
        service = SqlWaferSummaryService(self._engine)
        page = 1
        seen = 0
        while True:
            result = service.summarize(
                WaferSummaryRequest(
                    **work_item.context.model_dump(mode="json"),
                    page=page,
                    page_size=200,
                    sort_by=selected.sort_by,
                    sort_direction=selected.sort_direction,
                )
            )
            if page == 1:
                yield from self._result_context_rows(
                    work_item,
                    result,
                    extras={
                        "sort_by": result.sort_by,
                        "sort_direction": result.sort_direction,
                    },
                )
            for item in result.items:
                base = {
                    "dataset_id": item.dataset_id,
                    "version_no": item.version_no,
                    "group_key": f"LOT:{item.lot_id}|WAFER:{item.wafer_id}",
                }
                yield self._analysis_row(
                    "WAFER_SUMMARY",
                    item,
                    exclude=("parameters",),
                    metric_value=item.yield_rate,
                    **base,
                )
                for parameter in item.parameters:
                    yield self._analysis_row(
                        "WAFER_PARAMETER",
                        parameter,
                        parameter=parameter.parameter,
                        metric_value=parameter.mean,
                        **base,
                    )
            seen += len(result.items)
            if seen >= result.total or not result.items:
                break
            page += 1

    def _iter_analysis_report_rows(
        self,
        work_item: AnalyticsExportWorkItem,
        config: AnalyticsExportAnalysisConfig,
    ) -> Iterator[tuple[ExportCell, ...]]:
        routes = {
            "ANALYTICS_OVERVIEW": lambda: self._overview_report_rows(work_item, config),
            "PARAMETER_ANALYSIS": lambda: self._parameter_report_rows(
                work_item, config
            ),
            "PARAMETER_RELATIONSHIP": lambda: self._relationship_report_rows(
                work_item, config
            ),
            "SPATIAL_ANALYSIS": lambda: self._spatial_report_rows(work_item, config),
            "FT_QUALITY": lambda: self._quality_report_rows(work_item, config),
            "WAFER_SUMMARY": lambda: self._wafer_summary_report_rows(work_item, config),
        }
        route = routes.get(work_item.template_code)
        if route is None:
            raise DomainError(
                "ANALYTICS_EXPORT_TEMPLATE_RENDERER_UNAVAILABLE",
                "the requested analytics report does not have a server renderer",
                409,
            )
        yield from route()

    @staticmethod
    def _dataset_item_rows(
        connection: Connection, dataset_version_id: int
    ) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            connection.execute(
                text(
                    "SELECT DISTINCT tid.test_item_id,tid.raw_item_name,"
                    "tid.canonical_parameter_code,tid.step_code,tid.sequence_no,"
                    "tid.unit_code,tid.program_lsl,tid.program_usl,tid.condition_json "
                    "FROM dataset.dataset_version_run dvr "
                    "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                    "JOIN mdm.test_item_definition tid "
                    "ON tid.program_version_id=tr.program_version_id "
                    "WHERE dvr.dataset_version_id=:dataset_version_id "
                    "AND tid.is_analysis_parameter=1 AND tid.raw_item_name IS NOT NULL"
                ),
                {"dataset_version_id": dataset_version_id},
            )
            .mappings()
            .all()
        )

    @staticmethod
    def _source_run_ids(
        connection: Connection,
        dataset_version_id: int,
        selected: tuple[str, ...],
    ) -> tuple[int, ...] | None:
        if not selected:
            return None
        rows = (
            connection.execute(
                text(
                    "SELECT DISTINCT tr.run_id,tr.metadata_json,(SELECT sd.source_id FROM test.ft_run_detail sd WHERE sd.run_id=tr.run_id) AS source_id FROM "
                    "dataset.dataset_version_run dvr JOIN test.test_run tr "
                    "ON tr.processing_run_id=dvr.processing_run_id "
                    "WHERE dvr.dataset_version_id=:dataset_version_id"
                ),
                {"dataset_version_id": dataset_version_id},
            )
            .mappings()
            .all()
        )
        wanted = set(selected)
        return tuple(
            sorted(
                int(row["run_id"]) for row in rows if _source_identity(row) in wanted
            )
        )

    @staticmethod
    def _parameter_ids(
        item_rows: tuple[Mapping[str, Any], ...], selected: tuple[str, ...]
    ) -> tuple[int, ...]:
        if not selected:
            return ()
        wanted = set(selected)
        identities: dict[str, set[tuple[object, ...]]] = defaultdict(set)
        ids: dict[str, list[int]] = defaultdict(list)
        for row in item_rows:
            name = str(row["raw_item_name"])
            if name not in wanted:
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
        missing = sorted(wanted - set(ids))
        incompatible = sorted(
            name for name, values in identities.items() if len(values) != 1
        )
        if missing or incompatible:
            raise DomainError(
                "ANALYSIS_PARAMETER_INCOMPATIBLE",
                "one or more selected parameters are missing or ambiguous in export",
                409,
                details=[{"missing": missing, "incompatible": incompatible}],
            )
        return tuple(sorted({item for name in selected for item in ids[name]}))

    @staticmethod
    def _condition_item_ids(
        item_rows: tuple[Mapping[str, Any], ...], selected: tuple[str, ...]
    ) -> tuple[int, ...] | None:
        if not selected:
            return None
        wanted = set(selected)
        return tuple(
            sorted(
                int(row["test_item_id"])
                for row in item_rows
                if _condition_text(row["condition_json"]) in wanted
            )
        )

    @staticmethod
    def _filter_sql(
        work_item: AnalyticsExportWorkItem,
        *,
        source_run_ids: tuple[int, ...] | None,
        condition_item_ids: tuple[int, ...] | None,
    ) -> tuple[str, dict[str, object], tuple[str, ...]]:
        if work_item.export_scope.value == "FULL_DATASET":
            return "", {}, ()
        filters = work_item.context.filters
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

    def _detail_scope_filter_sql(
        self,
        request: AnalyticsDetailRequest,
        item_rows: tuple[Mapping[str, Any], ...],
    ) -> tuple[str, dict[str, object], tuple[str, ...]]:
        """Replay only the typed Detail filters frozen by CURRENT_PAGE."""

        clauses: list[str] = []
        parameters: dict[str, object] = {}
        expanding: list[str] = []
        if request.evaluation_filter is not None:
            evaluation_filter = request.evaluation_filter
            clause = (
                "EXISTS(SELECT 1 FROM test.measurement risk_m "
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
            parameters["detail_evaluation_type"] = evaluation_filter.evaluation_type
            parameters["detail_evaluation_results"] = tuple(
                evaluation_filter.evaluation_results
            )
            expanding.append("detail_evaluation_results")
            if evaluation_filter.rule_code is None:
                clause += " AND risk_rs.rule_code IS NULL"
            else:
                clause += " AND risk_rs.rule_code=:detail_rule_code"
                parameters["detail_rule_code"] = evaluation_filter.rule_code
            if evaluation_filter.rule_version is None:
                clause += " AND risk_rv.version_code IS NULL"
            else:
                clause += " AND risk_rv.version_code=:detail_rule_version"
                parameters["detail_rule_version"] = evaluation_filter.rule_version
            risk_parameter_ids = self._parameter_ids(
                item_rows, tuple(request.parameters)
            )
            if risk_parameter_ids:
                clause += " AND risk_m.test_item_id IN :detail_parameter_ids"
                parameters["detail_parameter_ids"] = risk_parameter_ids
                expanding.append("detail_parameter_ids")
            clauses.append(clause + ")")

        if request.measurement_filter is not None:
            measurement_filter = request.measurement_filter
            measurement_parameter_ids = self._parameter_ids(
                item_rows, (measurement_filter.parameter,)
            )
            clause = (
                "EXISTS(SELECT 1 FROM test.measurement aggregate_m "
                "WHERE aggregate_m.unit_id=ur.unit_id "
                "AND aggregate_m.test_item_id IN :aggregate_parameter_ids "
                "AND aggregate_m.measurement_status='MEASURED' "
                "AND aggregate_m.value_numeric IS NOT NULL"
            )
            parameters["aggregate_parameter_ids"] = measurement_parameter_ids
            expanding.append("aggregate_parameter_ids")
            if measurement_filter.lower_bound is not None:
                clause += (
                    " AND aggregate_m.value_numeric>=:aggregate_lower_bound"
                    if measurement_filter.lower_inclusive
                    else " AND aggregate_m.value_numeric>:aggregate_lower_bound"
                )
                parameters["aggregate_lower_bound"] = measurement_filter.lower_bound
            if measurement_filter.upper_bound is not None:
                clause += (
                    " AND aggregate_m.value_numeric<=:aggregate_upper_bound"
                    if measurement_filter.upper_inclusive
                    else " AND aggregate_m.value_numeric<:aggregate_upper_bound"
                )
                parameters["aggregate_upper_bound"] = measurement_filter.upper_bound
            clauses.append(clause + ")")

        return (
            " AND " + " AND ".join(clauses) if clauses else "",
            parameters,
            tuple(expanding),
        )

    @staticmethod
    def _base_join() -> str:
        return (
            " FROM dataset.dataset_version_run dvr "
            "JOIN dataset.dataset_version dv "
            "ON dv.dataset_version_id=dvr.dataset_version_id "
            "JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
            "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
            "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
            "LEFT JOIN mdm.test_program_version pv "
            "ON pv.program_version_id=tr.program_version_id "
        )

    def _unit_stream(
        self,
        connection: Connection,
        work_item: AnalyticsExportWorkItem,
        dataset_version_id: int,
        *,
        include_parameters: bool,
        detail_request: AnalyticsDetailRequest | None = None,
    ):
        item_rows = self._dataset_item_rows(connection, dataset_version_id)
        if work_item.export_scope.value == "FULL_DATASET":
            source_run_ids = None
            condition_item_ids = None
        else:
            source_run_ids = self._source_run_ids(
                connection,
                dataset_version_id,
                tuple(work_item.context.filters.source_ids),
            )
            condition_item_ids = self._condition_item_ids(
                item_rows, tuple(work_item.context.filters.test_conditions)
            )
        filter_sql, parameters, expanding = self._filter_sql(
            work_item,
            source_run_ids=source_run_ids,
            condition_item_ids=condition_item_ids,
        )
        if detail_request is not None:
            detail_filter_sql, detail_parameters, detail_expanding = (
                self._detail_scope_filter_sql(detail_request, item_rows)
            )
            filter_sql += detail_filter_sql
            parameters.update(detail_parameters)
            expanding += detail_expanding
        parameter_ids = self._parameter_ids(
            item_rows,
            tuple(work_item.context.parameters) if include_parameters else (),
        )
        select = (
            "SELECT dv.dataset_id,dv.version_no,d.test_stage,ur.unit_id,"
            "ur.logical_unit_key,tr.lot_id,COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
            "ur.x_coord,ur.y_coord,ur.soft_bin,ur.hard_bin,ur.overall_result,"
            "ur.source_row_no,tr.run_id,tr.metadata_json,(SELECT sd.source_id FROM test.ft_run_detail sd WHERE sd.run_id=tr.run_id) AS source_id,tr.tester_id,"
            "pv.version_code AS program_version"
        )
        joins = ""
        order = (
            _detail_order_sql(detail_request)
            if detail_request is not None
            else " ORDER BY tr.run_id,ISNULL(ur.unit_sequence,ur.unit_id),ur.unit_id"
        )
        if parameter_ids:
            select += (
                ",m.measurement_id,tid.raw_item_name,m.value_numeric,m.value_text,"
                "m.measurement_status,tid.unit_code,tid.program_lsl,tid.program_usl"
            )
            joins = (
                " LEFT JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "AND m.test_item_id IN :parameter_ids "
                "LEFT JOIN mdm.test_item_definition tid "
                "ON tid.test_item_id=m.test_item_id "
            )
            parameters["parameter_ids"] = parameter_ids
            expanding += ("parameter_ids",)
            order += ",tid.sequence_no,m.measurement_id"
        statement = _statement(
            select
            + self._base_join()
            + joins
            + " WHERE dvr.dataset_version_id=:dataset_version_id"
            + filter_sql
            + order,
            expanding,
        )
        parameters["dataset_version_id"] = dataset_version_id
        return (
            connection.execution_options(stream_results=True)
            .execute(statement, parameters)
            .mappings()
        )

    @staticmethod
    def _fixed_values(row: Mapping[str, Any]) -> tuple[ExportCell, ...]:
        return (
            int(row["dataset_id"]),
            int(row["version_no"]),
            str(row["test_stage"]),
            int(row["unit_id"]),
            f"UNIT:{int(row['unit_id'])}",
            str(row["logical_unit_key"]),
            str(row["lot_id"]),
            str(row["wafer_id"]) if row["wafer_id"] is not None else None,
            int(row["x_coord"]) if row["x_coord"] is not None else None,
            int(row["y_coord"]) if row["y_coord"] is not None else None,
            str(row["soft_bin"]) if row["soft_bin"] is not None else None,
            str(row["hard_bin"]) if row["hard_bin"] is not None else None,
            str(row["overall_result"]),
            int(row["source_row_no"]) if row["source_row_no"] is not None else None,
            _source_identity(row),
            str(row["tester_id"]) if row["tester_id"] is not None else None,
            (
                str(row["program_version"])
                if row["program_version"] is not None
                else None
            ),
        )

    @staticmethod
    def _measurement_values(row: Mapping[str, Any]) -> tuple[ExportCell, ...]:
        value: ExportCell
        if row["value_numeric"] is not None:
            value = _finite_float(row["value_numeric"], field="export measurement")
        elif row["value_text"] is not None:
            value = str(row["value_text"])
        else:
            value = None
        return (
            value,
            str(row["measurement_status"]),
            str(row["unit_code"]) if row["unit_code"] is not None else None,
            _finite_float(row["program_lsl"], field="export program LSL"),
            _finite_float(row["program_usl"], field="export program USL"),
        )

    def _iter_detail_rows(
        self, work_item: AnalyticsExportWorkItem
    ) -> Iterator[tuple[ExportCell, ...]]:
        parameters = tuple(work_item.context.parameters)
        empty_measurement = (None, None, None, None, None)
        unit_ordinal = 0
        first = ((work_item.page or 1) - 1) * (work_item.page_size or 0)
        last = first + (work_item.page_size or 0)
        detail_request: AnalyticsDetailRequest | None = None
        dataset_version_ids = work_item.dataset_version_ids
        if work_item.export_scope.value == "CURRENT_PAGE":
            try:
                public_detail_state, focus_dataset_id = (
                    resolve_current_page_detail_state(
                        work_item.chart_config, work_item.display_config
                    )
                )
                detail_state = (
                    work_item.current_page_detail_state or public_detail_state
                )
                if detail_state != public_detail_state:
                    raise ValueError(
                        "typed CURRENT_PAGE Detail state does not match presentation"
                    )
                if (
                    work_item.display_config.get("page") != work_item.page
                    or work_item.display_config.get("page_size") != work_item.page_size
                ):
                    raise ValueError(
                        "CURRENT_PAGE page bounds do not match the frozen display"
                    )
                detail_request = AnalyticsDetailRequest.model_validate(
                    {
                        **work_item.context.model_dump(mode="json"),
                        "focus_dataset_id": focus_dataset_id,
                        "page": work_item.page,
                        "page_size": work_item.page_size,
                        **detail_state.model_dump(mode="json"),
                    }
                )
            except ValueError as exc:
                raise DomainError(
                    "ANALYTICS_EXPORT_CURRENT_PAGE_STATE_INVALID",
                    "the queued CURRENT_PAGE export cannot reproduce the frozen Detail view",
                    409,
                ) from exc
            dataset_version_ids = tuple(
                dataset_version_id
                for reference, dataset_version_id in zip(
                    work_item.context.datasets,
                    work_item.dataset_version_ids,
                    strict=True,
                )
                if reference.dataset_id == focus_dataset_id
            )
            if len(dataset_version_ids) != 1:
                raise DomainError(
                    "ANALYTICS_EXPORT_CURRENT_PAGE_STATE_INVALID",
                    "the frozen Detail Dataset does not match the queued Dataset Versions",
                    409,
                )
        with self._engine.connect() as connection:
            if parameters:
                all_items = tuple(
                    row
                    for dataset_version_id in dataset_version_ids
                    for row in self._dataset_item_rows(connection, dataset_version_id)
                )
                self._parameter_ids(all_items, parameters)
            for dataset_version_id in dataset_version_ids:
                current_unit_id: int | None = None
                fixed: tuple[ExportCell, ...] | None = None
                values: dict[str, tuple[ExportCell, ...]] = {}
                for row in self._unit_stream(
                    connection,
                    work_item,
                    dataset_version_id,
                    include_parameters=bool(parameters),
                    detail_request=detail_request,
                ):
                    unit_id = int(row["unit_id"])
                    if current_unit_id is not None and unit_id != current_unit_id:
                        if work_item.export_scope.value != "CURRENT_PAGE" or (
                            first <= unit_ordinal < last
                        ):
                            assert fixed is not None
                            yield fixed + tuple(
                                item
                                for parameter in parameters
                                for item in values.get(parameter, empty_measurement)
                            )
                        unit_ordinal += 1
                        if (
                            work_item.export_scope.value == "CURRENT_PAGE"
                            and unit_ordinal >= last
                        ):
                            current_unit_id = None
                            fixed = None
                            values = {}
                            break
                        values = {}
                    if current_unit_id != unit_id:
                        current_unit_id = unit_id
                        fixed = self._fixed_values(row)
                    measurement_id = row.get("measurement_id")
                    if measurement_id is not None:
                        parameter = str(row["raw_item_name"])
                        if parameter in values:
                            raise DomainError(
                                "ANALYTICS_EXPORT_MEASUREMENT_AMBIGUOUS",
                                "one unit has more than one selected measurement identity",
                                409,
                            )
                        values[parameter] = self._measurement_values(row)
                if current_unit_id is not None:
                    if work_item.export_scope.value != "CURRENT_PAGE" or (
                        first <= unit_ordinal < last
                    ):
                        assert fixed is not None
                        yield fixed + tuple(
                            item
                            for parameter in parameters
                            for item in values.get(parameter, empty_measurement)
                        )
                    unit_ordinal += 1
                if (
                    work_item.export_scope.value == "CURRENT_PAGE"
                    and unit_ordinal >= last
                ):
                    break

    def _iter_overview_rows(
        self, work_item: AnalyticsExportWorkItem
    ) -> Iterator[tuple[ExportCell, ...]]:
        with self._engine.connect() as connection:
            for dataset_version_id in work_item.dataset_version_ids:
                counters: Counter[str] = Counter()
                bin_counts: Counter[str] = Counter()
                wafer_counts: dict[tuple[str, str | None], Counter[str]] = defaultdict(
                    Counter
                )
                identity: tuple[int, int] | None = None
                for row in self._unit_stream(
                    connection,
                    work_item,
                    dataset_version_id,
                    include_parameters=False,
                ):
                    identity = (int(row["dataset_id"]), int(row["version_no"]))
                    result = str(row["overall_result"])
                    counters[result] += 1
                    bin_code = str(row["soft_bin"] or row["hard_bin"] or "UNKNOWN")
                    bin_counts[bin_code] += 1
                    wafer = (
                        str(row["wafer_id"]) if row["wafer_id"] is not None else None
                    )
                    wafer_counts[(str(row["lot_id"]), wafer)][result] += 1
                if identity is None:
                    # Resolve identity even when the filters select zero units.
                    identity_row = (
                        connection.execute(
                            text(
                                "SELECT dataset_id,version_no FROM dataset.dataset_version "
                                "WHERE dataset_version_id=:dataset_version_id"
                            ),
                            {"dataset_version_id": dataset_version_id},
                        )
                        .mappings()
                        .one()
                    )
                    identity = (
                        int(identity_row["dataset_id"]),
                        int(identity_row["version_no"]),
                    )
                dataset_id, version_no = identity
                total = sum(counters.values())
                known = counters["PASS"] + counters["FAIL"]
                yield_rate = counters["PASS"] / known if known else None
                yield (
                    "TOTAL",
                    dataset_id,
                    version_no,
                    None,
                    None,
                    None,
                    None,
                    total,
                    known,
                    yield_rate,
                    1.0 if total else None,
                )
                for result in ("PASS", "FAIL", "UNKNOWN", "ABORT"):
                    count = counters[result]
                    yield (
                        "RESULT",
                        dataset_id,
                        version_no,
                        None,
                        None,
                        None,
                        result,
                        count,
                        known,
                        yield_rate,
                        count / total if total else None,
                    )
                for bin_code, count in sorted(
                    bin_counts.items(), key=lambda item: (-item[1], item[0])
                ):
                    yield (
                        "BIN",
                        dataset_id,
                        version_no,
                        None,
                        None,
                        bin_code,
                        None,
                        count,
                        known,
                        yield_rate,
                        count / total if total else None,
                    )
                for (lot_id, wafer_id), results in sorted(wafer_counts.items()):
                    wafer_total = sum(results.values())
                    wafer_known = results["PASS"] + results["FAIL"]
                    yield (
                        "LOT_WAFER",
                        dataset_id,
                        version_no,
                        lot_id,
                        wafer_id,
                        None,
                        None,
                        wafer_total,
                        wafer_known,
                        results["PASS"] / wafer_known if wafer_known else None,
                        wafer_total / total if total else None,
                    )
