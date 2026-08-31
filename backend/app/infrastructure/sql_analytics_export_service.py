from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from app.core.errors import DomainError
from app.domain.analysis_rule_pinning import AnalysisRuleRequirement
from app.domain.analytics import (
    AnalyticsContextRequest,
    AnalyticsDatasetReference,
    AnalyticsFilters,
)
from app.domain.analytics_export_analysis import (
    analytics_export_analysis_parameters,
    analytics_export_required_rules,
    resolve_analytics_export_analysis_config,
)
from app.domain.analytics_exports import (
    ANALYTICS_EXPORT_CONTRACT_VERSION,
    ANALYTICS_EXPORT_WORKER_CONTRACT_VERSION,
    CURRENT_PAGE_DETAIL_PAYLOAD_KEY,
    AnalyticsExportArtifactMetadata,
    AnalyticsExportAvailability,
    AnalyticsExportDatasetRecord,
    AnalyticsExportDownloadMetadata,
    AnalyticsExportDownloadTarget,
    AnalyticsExportFormat,
    AnalyticsExportPage,
    AnalyticsExportRecord,
    AnalyticsExportScope,
    AnalyticsExportStatus,
    CancelAnalyticsExportRequest,
    CreateAnalyticsExportRequest,
    freeze_current_page_detail_state,
    replay_stored_current_page_detail_state,
    resolve_analytics_export_template,
)
from app.domain.auth import Principal
from app.domain.saved_analyses import (
    SavedAnalysisRuleContext,
    canonical_json,
    saved_analysis_hashes,
    validate_analysis_presentation_config,
)
from app.infrastructure.analysis_rule_pinning import (
    ApprovedRuleParameterResolver,
    validate_required_analysis_rules,
    verified_merged_rule_context,
)
from app.infrastructure.analytics_export_files import (
    AnalyticsExportPathPolicy,
    UnsafeAnalyticsExportPath,
)
from app.infrastructure.formal_spec_context_resolver import (
    resolve_formal_spec_context,
)
from app.infrastructure.sql_analysis_rule_service import SqlAnalysisRuleService

RuleContextResolver = Callable[
    [Connection, tuple[Mapping[str, Any], ...], AnalyticsContextRequest],
    SavedAnalysisRuleContext,
]

_ARTIFACT_CONTRACTS = {
    "CSV": ({"text/csv", "text/csv; charset=utf-8"}, {".csv"}),
    "XLSX": (
        {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        {".xlsx"},
    ),
    "PNG": ({"image/png"}, {".png"}),
    "BIN_TXT": ({"text/plain", "application/octet-stream"}, {".bin", ".txt"}),
    "HTML": ({"text/html", "text/html; charset=utf-8"}, {".html", ".htm"}),
    "PDF": ({"application/pdf"}, {".pdf"}),
}


def _is_admin(principal: Principal) -> bool:
    return "SYSTEM_ADMIN" in principal.roles


def _row_version_hex(value: Any) -> str:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        if len(value) != 8:
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "export row version has an invalid database representation",
                409,
            )
        return value.hex().upper()
    rendered = str(value).strip().upper()
    if len(rendered) == 18 and rendered.startswith("0X"):
        rendered = rendered[2:]
    if len(rendered) != 16 or any(
        character not in "0123456789ABCDEF" for character in rendered
    ):
        raise DomainError(
            "ANALYTICS_EXPORT_INTEGRITY_ERROR",
            "export row version has an invalid database representation",
            409,
        )
    return rendered


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _utc(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise DomainError(
            "ANALYTICS_EXPORT_ARTIFACT_INTEGRITY_ERROR",
            "artifact timestamp is invalid",
            409,
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SqlAnalyticsExportService:
    """Queued analytics exports over sql2014_0020 without copying Measurements."""

    def __init__(
        self,
        engine: Engine,
        *,
        rule_context_resolver: RuleContextResolver | None = None,
        approved_rule_resolver: ApprovedRuleParameterResolver | None = None,
        path_policy: AnalyticsExportPathPolicy | None = None,
    ) -> None:
        self._engine = engine
        self._rule_context_resolver = (
            rule_context_resolver or self._default_rule_context
        )
        self._approved_rule_resolver = (
            approved_rule_resolver
            or SqlAnalysisRuleService(engine).approved_rule_parameters
        )
        if path_policy is None:
            configured_root = os.getenv("TMS_ANALYTICS_EXPORT_ROOT", "").strip()
            if not configured_root:
                if os.getenv("TMS_ENV", "").strip().lower() == "production":
                    raise RuntimeError(
                        "TMS_ANALYTICS_EXPORT_ROOT is required in production"
                    )
                configured_root = r"F:\CP-FT数据分析\data\analytics_exports"
            path_policy = AnalyticsExportPathPolicy(Path(configured_root))
        self._path_policy = path_policy

    @staticmethod
    def _require_permissions(principal: Principal) -> None:
        if not principal.can("EXPORT_DATA") or not principal.can("DATASET_READ"):
            raise DomainError(
                "PERMISSION_DENIED",
                "analytics exports require DATASET_READ and EXPORT_DATA",
                403,
            )

    @staticmethod
    def _can_read_dataset(row: Mapping[str, Any], principal: Principal) -> bool:
        return bool(
            _is_admin(principal)
            or int(row["owner_user_id"]) == principal.user_id
            or (
                str(row.get("business_domain") or "") == "PRODUCTION"
                and str(row["status"]) == "PUBLISHED"
                and bool(row["is_current"])
            )
        )

    @staticmethod
    def _dataset_row(
        connection: Connection,
        reference: AnalyticsDatasetReference,
        *,
        hold_lock: bool,
    ) -> Mapping[str, Any]:
        lock_hint = " WITH (HOLDLOCK)" if hold_lock else ""
        row = (
            connection.execute(
                text(
                    "SELECT dv.dataset_version_id,dv.dataset_id,dv.version_no,"
                    "dv.status,dv.is_current,dv.spec_set_id,d.test_stage,"
                    "d.owner_user_id,d.supplier_id,d.product_id,"
                    "b.business_domain,ss.version_code AS spec_version "
                    "FROM dataset.dataset_version dv"
                    + lock_hint
                    + " JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                    "LEFT JOIN ingestion.import_batch b "
                    "ON b.import_batch_id=dv.input_batch_id "
                    "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=dv.spec_set_id "
                    "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                ),
                {
                    "dataset_id": reference.dataset_id,
                    "version_no": reference.version_no,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DomainError(
                "ANALYTICS_EXPORT_DATASET_NOT_FOUND",
                "one selected Dataset Version was not found",
                404,
            )
        return row

    def _dataset_rows(
        self,
        connection: Connection,
        references: Sequence[AnalyticsDatasetReference],
        principal: Principal,
        *,
        require_current: bool,
        hold_lock: bool,
    ) -> tuple[Mapping[str, Any], ...]:
        rows = tuple(
            self._dataset_row(connection, reference, hold_lock=hold_lock)
            for reference in references
        )
        for row in rows:
            if not self._can_read_dataset(row, principal):
                raise DomainError(
                    "ANALYTICS_EXPORT_DATASET_ACCESS_DENIED",
                    "one selected Dataset Version is outside the user's access scope",
                    403,
                )
            if require_current and (
                str(row["status"]) != "PUBLISHED" or not bool(row["is_current"])
            ):
                raise DomainError(
                    "ANALYTICS_EXPORT_DATASET_NOT_CURRENT",
                    "analytics export accepts only Current Published Dataset Versions",
                    409,
                )
        stages = {str(row["test_stage"]) for row in rows}
        if len(stages) != 1 or not stages.issubset({"CP", "FT"}):
            raise DomainError(
                "ANALYTICS_EXPORT_STAGE_INCOMPATIBLE",
                "one export may contain only CP or only FT Dataset Versions",
                409,
            )
        if len(rows) > 1 and next(iter(stages)) == "CP":
            spec_ids = {row["spec_set_id"] for row in rows}
            if None in spec_ids or len(spec_ids) != 1:
                raise DomainError(
                    "ANALYTICS_EXPORT_SPEC_INCOMPATIBLE",
                    "selected CP Dataset Versions lack one proven compatible Spec",
                    409,
                )
        return rows

    @staticmethod
    def _default_rule_context(
        connection: Connection,
        dataset_rows: tuple[Mapping[str, Any], ...],
        context: AnalyticsContextRequest,
    ) -> SavedAnalysisRuleContext:
        spec_versions = list(
            resolve_formal_spec_context(connection, dataset_rows, context).spec_versions
        )
        bin_versions: set[str] = set()
        for row in dataset_rows:
            bin_rows = (
                connection.execute(
                    text(
                        "SELECT DISTINCT bms.bin_mapping_set_id,bms.version_code "
                        "FROM dataset.dataset_version_run dvr "
                        "JOIN ingestion.processing_run pr "
                        "ON pr.processing_run_id=dvr.processing_run_id "
                        "JOIN test.test_run tr ON tr.processing_run_id=pr.processing_run_id "
                        "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "JOIN test.unit_bin_evaluation ube ON ube.unit_id=ur.unit_id "
                        "JOIN mdm.bin_mapping_set bms "
                        "ON bms.bin_mapping_set_id=ube.bin_mapping_set_id "
                        "WHERE dvr.dataset_version_id=:dataset_version_id"
                    ),
                    {"dataset_version_id": int(row["dataset_version_id"])},
                )
                .mappings()
                .all()
            )
            bin_versions.update(
                f"BIN:{int(item['bin_mapping_set_id'])}:{item['version_code']}"
                for item in bin_rows
            )
        return SavedAnalysisRuleContext(
            spec_versions=spec_versions,
            bin_mapping_versions=sorted(bin_versions),
            evaluation_rule_versions=[],
        )

    def _verified_rule_context(
        self,
        connection: Connection,
        dataset_rows: tuple[Mapping[str, Any], ...],
        context: AnalyticsContextRequest,
        requested: SavedAnalysisRuleContext,
        required_rules: tuple[AnalysisRuleRequirement, ...],
    ) -> SavedAnalysisRuleContext:
        current = self._rule_context_resolver(connection, dataset_rows, context)
        required_identities = validate_required_analysis_rules(
            required_rules, dataset_rows, self._approved_rule_resolver
        )
        return verified_merged_rule_context(
            current=current,
            requested=requested,
            required_identities=required_identities,
            stale_code="ANALYTICS_EXPORT_RULE_CONTEXT_STALE",
            stale_message=(
                "Spec, Bin or approved evaluation Rule context changed before queueing"
            ),
        )

    @staticmethod
    def _stored_filter_json(
        request: CreateAnalyticsExportRequest,
    ) -> tuple[str, Any, str]:
        hashes = saved_analysis_hashes(request)
        presentation_hash = validate_analysis_presentation_config(
            request.chart_config, request.display_config
        )
        current_page_detail_state = freeze_current_page_detail_state(
            request.export_scope,
            request.chart_config,
            request.display_config,
        )
        payload = {
            "artifact_ttl_hours": request.artifact_ttl_hours,
            "chart_config": request.chart_config,
            CURRENT_PAGE_DETAIL_PAYLOAD_KEY: (
                current_page_detail_state.model_dump(mode="json", by_alias=True)
                if current_page_detail_state is not None
                else None
            ),
            "display_config": request.display_config,
            "filters": hashes.normalized_filters,
            "page": request.page,
            "page_size": request.page_size,
            "parameters": list(hashes.normalized_parameters),
            "presentation_hash": presentation_hash,
            "request_reason_sha256": hashlib.sha256(
                request.reason.encode("utf-8")
            ).hexdigest(),
        }
        return canonical_json(payload), hashes, presentation_hash

    @staticmethod
    def _audit(
        connection: Connection,
        *,
        principal: Principal,
        operation: str,
        export_job_id: int,
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any] | None,
        reason: str,
    ) -> None:
        connection.execute(
            text(
                "INSERT governance.audit_log(actor,actor_user_id,operation,entity_type,"
                "entity_id,before_json,after_json,reason) VALUES(:actor,:actor_user_id,"
                ":operation,'ANALYTICS_EXPORT',:entity_id,:before_json,:after_json,:reason)"
            ),
            {
                "actor": principal.login_name,
                "actor_user_id": principal.user_id,
                "operation": operation,
                "entity_id": str(export_job_id),
                "before_json": canonical_json(before) if before is not None else None,
                "after_json": canonical_json(after) if after is not None else None,
                "reason": reason,
            },
        )

    def create(
        self, request: CreateAnalyticsExportRequest, principal: Principal
    ) -> AnalyticsExportRecord:
        self._require_permissions(principal)
        with self._engine.begin() as connection:
            dataset_rows = self._dataset_rows(
                connection,
                request.datasets,
                principal,
                require_current=True,
                hold_lock=True,
            )
            try:
                resolve_analytics_export_template(
                    request.template_code,
                    request.template_version,
                    request.export_scope,
                    request.export_format,
                    test_stage=str(dataset_rows[0]["test_stage"]),
                )
            except ValueError as exc:
                raise DomainError(
                    "ANALYTICS_EXPORT_TEMPLATE_INCOMPATIBLE",
                    "registered export template is incompatible with the Dataset Stage",
                    409,
                ) from exc
            try:
                analysis_config = resolve_analytics_export_analysis_config(
                    request.template_code, request.chart_config
                )
                required_rules = analytics_export_required_rules(analysis_config)
            except ValueError as exc:
                raise DomainError(
                    "ANALYTICS_EXPORT_ANALYSIS_CONFIG_INVALID",
                    "versioned report analysis config is invalid or has incomplete exact Rule references",
                    409,
                ) from exc
            parameters = tuple(
                sorted(
                    set(request.parameters)
                    | set(analytics_export_analysis_parameters(analysis_config))
                )
            )
            frozen_context = AnalyticsContextRequest(
                datasets=request.datasets,
                filters=request.filters,
                parameters=list(parameters),
            )
            rule_context = self._verified_rule_context(
                connection,
                dataset_rows,
                frozen_context,
                request.rule_context,
                required_rules,
            )
            filter_json, hashes, presentation_hash = self._stored_filter_json(request)
            rule_json = canonical_json(rule_context.model_dump(mode="json"))

            existing = (
                connection.execute(
                    text(
                        "SELECT TOP (1) export_job_id,contract_version "
                        "FROM delivery.export_job WITH (UPDLOCK,HOLDLOCK) "
                        "WHERE requested_by=:user_id "
                        "AND idempotency_key=:idempotency_key"
                    ),
                    {
                        "user_id": principal.user_id,
                        "idempotency_key": request.idempotency_key,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if (
                    str(existing["contract_version"])
                    != ANALYTICS_EXPORT_CONTRACT_VERSION
                ):
                    raise DomainError(
                        "ANALYTICS_EXPORT_IDEMPOTENCY_CONFLICT",
                        "idempotency key belongs to a different export contract",
                        409,
                    )
                existing = self._load_record(
                    connection,
                    int(existing["export_job_id"]),
                    principal,
                    idempotent_replay=True,
                )
                self._assert_idempotent_scope(existing, request, hashes, rule_context)
                return existing

            try:
                export_job_id = int(
                    connection.execute(
                        text(
                            "INSERT delivery.export_job(requested_by,dataset_version_id,"
                            "evaluation_run_id,export_scope,export_format,template_code,"
                            "template_version,filter_json,status,contract_version,filter_hash,"
                            "context_hash,rule_context_json,idempotency_key,exported_row_count) "
                            "OUTPUT INSERTED.export_job_id VALUES(:requested_by,"
                            ":dataset_version_id,NULL,:export_scope,:export_format,"
                            ":template_code,:template_version,:filter_json,'QUEUED',"
                            ":contract_version,:filter_hash,:context_hash,:rule_context_json,"
                            ":idempotency_key,NULL)"
                        ),
                        {
                            "requested_by": principal.user_id,
                            "dataset_version_id": int(
                                dataset_rows[0]["dataset_version_id"]
                            ),
                            "export_scope": request.export_scope.value,
                            "export_format": request.export_format.value,
                            "template_code": request.template_code,
                            "template_version": request.template_version,
                            "filter_json": filter_json,
                            "contract_version": ANALYTICS_EXPORT_CONTRACT_VERSION,
                            "filter_hash": hashes.filter_hash,
                            "context_hash": hashes.context_hash,
                            "rule_context_json": rule_json,
                            "idempotency_key": request.idempotency_key,
                        },
                    ).scalar_one()
                )
            except IntegrityError as exc:
                raise DomainError(
                    "ANALYTICS_EXPORT_WRITE_CONFLICT",
                    "export request conflicted while queueing; retry the same key",
                    409,
                ) from exc

            for ordinal, row in enumerate(dataset_rows, start=1):
                connection.execute(
                    text(
                        "INSERT delivery.export_job_dataset(export_job_id,"
                        "dataset_version_id,ordinal_no) VALUES(:export_job_id,"
                        ":dataset_version_id,:ordinal_no)"
                    ),
                    {
                        "export_job_id": export_job_id,
                        "dataset_version_id": int(row["dataset_version_id"]),
                        "ordinal_no": ordinal,
                    },
                )
            self._audit(
                connection,
                principal=principal,
                operation="ANALYTICS_EXPORT_QUEUE",
                export_job_id=export_job_id,
                before=None,
                after={
                    "context_hash": hashes.context_hash,
                    "dataset_version_ids": [
                        int(row["dataset_version_id"]) for row in dataset_rows
                    ],
                    "export_format": request.export_format.value,
                    "export_scope": request.export_scope.value,
                    "presentation_hash": presentation_hash,
                    "status": "QUEUED",
                    "template_code": request.template_code,
                    "template_version": request.template_version,
                },
                reason=request.reason,
            )
            return self._load_record(
                connection, export_job_id, principal, idempotent_replay=False
            )

    @staticmethod
    def _assert_idempotent_scope(
        existing: AnalyticsExportRecord,
        request: CreateAnalyticsExportRequest,
        hashes: Any,
        effective_rule_context: SavedAnalysisRuleContext,
    ) -> None:
        existing_datasets = [
            (item.dataset_id, item.version_no) for item in existing.datasets
        ]
        requested_datasets = [
            (item.dataset_id, item.version_no) for item in request.datasets
        ]
        same = (
            existing_datasets == requested_datasets
            and existing.contract_version == request.contract_version
            and existing.export_scope == request.export_scope.value
            and existing.export_format == request.export_format.value
            and existing.template_code == request.template_code
            and existing.template_version == request.template_version
            and existing.filter_hash == hashes.filter_hash
            and existing.context_hash == hashes.context_hash
            and existing.rule_context.model_dump(mode="json")
            == effective_rule_context.model_dump(mode="json")
            and existing.chart_config == request.chart_config
            and existing.display_config == request.display_config
            and existing.presentation_hash
            == validate_analysis_presentation_config(
                request.chart_config, request.display_config
            )
            and existing.artifact_ttl_hours == request.artifact_ttl_hours
            and existing.page == request.page
            and existing.page_size == request.page_size
            and existing.request_reason_sha256
            == hashlib.sha256(request.reason.encode("utf-8")).hexdigest()
        )
        if not same:
            raise DomainError(
                "ANALYTICS_EXPORT_IDEMPOTENCY_CONFLICT",
                "idempotency key is already bound to a different export request",
                409,
            )

    @staticmethod
    def _job_row(
        connection: Connection,
        export_job_id: int,
        principal: Principal,
        *,
        hold_lock: bool = False,
    ) -> Mapping[str, Any]:
        lock_hint = " WITH (UPDLOCK,HOLDLOCK)" if hold_lock else ""
        row = (
            connection.execute(
                text(
                    "SELECT export_job_id,requested_by,dataset_version_id,export_scope,"
                    "export_format,template_code,template_version,filter_json,status,"
                    "requested_at_utc,started_at_utc,finished_at_utc,contract_version,"
                    "filter_hash,context_hash,rule_context_json,idempotency_key,"
                    "exported_row_count,error_message,row_version FROM delivery.export_job"
                    + lock_hint
                    + " WHERE export_job_id=:export_job_id AND "
                    "contract_version=:contract_version AND "
                    "(requested_by=:user_id OR :is_admin=1)"
                ),
                {
                    "export_job_id": export_job_id,
                    "user_id": principal.user_id,
                    "is_admin": int(_is_admin(principal)),
                    "contract_version": ANALYTICS_EXPORT_CONTRACT_VERSION,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DomainError(
                "ANALYTICS_EXPORT_NOT_FOUND", "analytics export was not found", 404
            )
        return row

    @staticmethod
    def _job_dataset_rows(
        connection: Connection, export_job_id: int
    ) -> tuple[Mapping[str, Any], ...]:
        rows = tuple(
            connection.execute(
                text(
                    "SELECT ejd.dataset_version_id,ejd.ordinal_no,dv.dataset_id,"
                    "dv.version_no,dv.status,dv.is_current,dv.spec_set_id,d.test_stage,"
                    "d.owner_user_id,d.supplier_id,d.product_id,b.business_domain,"
                    "ss.version_code AS spec_version FROM delivery.export_job_dataset ejd "
                    "JOIN dataset.dataset_version dv ON "
                    "dv.dataset_version_id=ejd.dataset_version_id "
                    "JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                    "LEFT JOIN ingestion.import_batch b "
                    "ON b.import_batch_id=dv.input_batch_id "
                    "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=dv.spec_set_id "
                    "WHERE ejd.export_job_id=:export_job_id ORDER BY ejd.ordinal_no"
                ),
                {"export_job_id": export_job_id},
            )
            .mappings()
            .all()
        )
        if not 1 <= len(rows) <= 8 or [int(row["ordinal_no"]) for row in rows] != list(
            range(1, len(rows) + 1)
        ):
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "export does not contain one to eight ordered Dataset Versions",
                409,
            )
        return rows

    def _load_record(
        self,
        connection: Connection,
        export_job_id: int,
        principal: Principal,
        *,
        idempotent_replay: bool,
        hold_lock: bool = False,
    ) -> AnalyticsExportRecord:
        row = self._job_row(connection, export_job_id, principal, hold_lock=hold_lock)
        dataset_rows = self._job_dataset_rows(connection, export_job_id)
        if int(row["dataset_version_id"]) != int(dataset_rows[0]["dataset_version_id"]):
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "legacy primary Dataset does not match export ordinal one",
                409,
            )
        for dataset_row in dataset_rows:
            if not self._can_read_dataset(dataset_row, principal):
                raise DomainError(
                    "ANALYTICS_EXPORT_ACCESS_REVOKED",
                    "access to one exported Dataset Version has been revoked",
                    403,
                )

        try:
            payload = json.loads(str(row["filter_json"]))
            expected_v1_keys = {
                "artifact_ttl_hours",
                "chart_config",
                "display_config",
                "filters",
                "page",
                "page_size",
                "parameters",
                "presentation_hash",
                "request_reason_sha256",
            }
            if not isinstance(payload, dict):
                raise TypeError("invalid stored export keys")
            payload_keys = frozenset(payload)
            if payload_keys not in {
                frozenset(expected_v1_keys),
                frozenset(expected_v1_keys | {CURRENT_PAGE_DETAIL_PAYLOAD_KEY}),
            }:
                raise ValueError("invalid stored export keys")
            export_scope = AnalyticsExportScope(str(row["export_scope"])).value
            export_format = AnalyticsExportFormat(str(row["export_format"])).value
            filters = AnalyticsFilters.model_validate(payload["filters"])
            context = AnalyticsContextRequest(
                datasets=[
                    AnalyticsDatasetReference(
                        dataset_id=int(item["dataset_id"]),
                        version_no=int(item["version_no"]),
                    )
                    for item in dataset_rows
                ],
                filters=filters,
                parameters=payload["parameters"],
            )
            hashes = saved_analysis_hashes(context)
            chart_config = payload["chart_config"]
            display_config = payload["display_config"]
            if not isinstance(chart_config, dict) or not isinstance(
                display_config, dict
            ):
                raise TypeError("invalid stored presentation configuration")
            presentation_hash = validate_analysis_presentation_config(
                chart_config, display_config
            )
            if str(payload["presentation_hash"]) != presentation_hash:
                raise ValueError("stored presentation hash does not reconcile")
            replay_stored_current_page_detail_state(
                payload,
                export_scope=export_scope,
                chart_config=chart_config,
                display_config=display_config,
            )
            rule_context = SavedAnalysisRuleContext.model_validate_json(
                str(row["rule_context_json"])
            )
            status = AnalyticsExportStatus(str(row["status"])).value
            artifact_ttl_hours = int(payload["artifact_ttl_hours"])
            request_reason_sha256 = str(payload["request_reason_sha256"])
            page = int(payload["page"]) if payload["page"] is not None else None
            page_size = (
                int(payload["page_size"]) if payload["page_size"] is not None else None
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "stored export context is invalid",
                409,
            ) from exc
        if not 1 <= artifact_ttl_hours <= 168:
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR", "stored export TTL is invalid", 409
            )
        if len(request_reason_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in request_reason_sha256
        ):
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "stored export request reason hash is invalid",
                409,
            )
        if (
            str(row["contract_version"]) != ANALYTICS_EXPORT_CONTRACT_VERSION
            or str(row["filter_hash"]) != hashes.filter_hash
            or str(row["context_hash"]) != hashes.context_hash
        ):
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "stored export hashes or contract version do not reconcile",
                409,
            )
        try:
            resolve_analytics_export_template(
                str(row["template_code"]),
                str(row["template_version"]),
                export_scope,
                export_format,
                test_stage=str(dataset_rows[0]["test_stage"]),
            )
        except ValueError as exc:
            raise DomainError(
                "ANALYTICS_EXPORT_INTEGRITY_ERROR",
                "stored export template contract is invalid",
                409,
            ) from exc
        failure_code: str | None = None
        failure_message: str | None = None
        stored_error = str(row["error_message"] or "").strip()
        if status == AnalyticsExportStatus.FAILED.value:
            code, separator, message = stored_error.partition(": ")
            if (
                separator
                and 1 <= len(code) <= 64
                and all(
                    character.isupper() or character.isdigit() or character == "_"
                    for character in code
                )
            ):
                failure_code = code
                failure_message = message[:900]
            else:
                failure_code = "ANALYTICS_EXPORT_WORKER_FAILED"
                failure_message = "analytics export generation failed"
        return AnalyticsExportRecord(
            export_job_id=int(row["export_job_id"]),
            requested_by=int(row["requested_by"]),
            contract_version=str(row["contract_version"]),
            worker_contract_version=ANALYTICS_EXPORT_WORKER_CONTRACT_VERSION,
            generation_mode="QUEUED_WORKER",
            status=status,
            export_scope=export_scope,
            export_format=export_format,
            template_code=str(row["template_code"]),
            template_version=str(row["template_version"]),
            datasets=tuple(
                AnalyticsExportDatasetRecord(
                    dataset_version_id=int(item["dataset_version_id"]),
                    dataset_id=int(item["dataset_id"]),
                    version_no=int(item["version_no"]),
                    ordinal_no=int(item["ordinal_no"]),
                    test_stage=str(item["test_stage"]),
                )
                for item in dataset_rows
            ),
            filters={
                key: [str(item) for item in value]
                for key, value in hashes.normalized_filters.items()
            },
            parameters=hashes.normalized_parameters,
            filter_hash=hashes.filter_hash,
            context_hash=hashes.context_hash,
            rule_context=rule_context,
            chart_config=chart_config,
            display_config=display_config,
            presentation_hash=presentation_hash,
            artifact_ttl_hours=artifact_ttl_hours,
            page=page,
            page_size=page_size,
            idempotency_key=str(row["idempotency_key"]),
            request_reason_sha256=request_reason_sha256,
            requested_at_utc=str(_timestamp(row["requested_at_utc"])),
            started_at_utc=_timestamp(row["started_at_utc"]),
            finished_at_utc=_timestamp(row["finished_at_utc"]),
            exported_row_count=(
                int(row["exported_row_count"])
                if row["exported_row_count"] is not None
                else None
            ),
            row_version=_row_version_hex(row["row_version"]),
            idempotent_replay=idempotent_replay,
            failure_code=failure_code,
            failure_message=failure_message,
        )

    def get(self, export_job_id: int, principal: Principal) -> AnalyticsExportRecord:
        self._require_permissions(principal)
        with self._engine.connect() as connection:
            return self._load_record(
                connection, export_job_id, principal, idempotent_replay=False
            )

    @staticmethod
    def _visible_where() -> str:
        return (
            "ej.contract_version='ANALYTICS_EXPORT_V1' AND "
            "(ej.requested_by=:user_id OR :is_admin=1) AND NOT EXISTS("
            "SELECT 1 FROM delivery.export_job_dataset denied "
            "JOIN dataset.dataset_version denied_dv ON "
            "denied_dv.dataset_version_id=denied.dataset_version_id "
            "JOIN dataset.dataset denied_d ON denied_d.dataset_id=denied_dv.dataset_id "
            "LEFT JOIN ingestion.import_batch denied_b ON "
            "denied_b.import_batch_id=denied_dv.input_batch_id "
            "WHERE denied.export_job_id=ej.export_job_id AND NOT("
            ":is_admin=1 OR denied_d.owner_user_id=:user_id OR "
            "(denied_b.business_domain='PRODUCTION' AND "
            "denied_dv.status='PUBLISHED' AND denied_dv.is_current=1)))"
        )

    def list_page(
        self, principal: Principal, *, page: int, page_size: int
    ) -> AnalyticsExportPage:
        self._require_permissions(principal)
        if page < 1 or not 1 <= page_size <= 100:
            raise DomainError(
                "ANALYTICS_EXPORT_PAGE_INVALID", "export page is out of range", 422
            )
        params = {
            "user_id": principal.user_id,
            "is_admin": int(_is_admin(principal)),
            "offset": (page - 1) * page_size,
            "page_size": page_size,
        }
        visible = self._visible_where()
        with self._engine.connect() as connection:
            total = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM delivery.export_job ej WHERE "
                        + visible
                    ),
                    params,
                ).scalar_one()
            )
            ids = tuple(
                int(value)
                for value in connection.execute(
                    text(
                        "SELECT ej.export_job_id FROM delivery.export_job ej WHERE "
                        + visible
                        + " ORDER BY ej.requested_at_utc DESC,ej.export_job_id DESC "
                        "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY"
                    ),
                    params,
                ).scalars()
            )
            items: list[AnalyticsExportRecord] = []
            integrity_blocked_job_ids: list[int] = []
            for export_job_id in ids:
                try:
                    items.append(
                        self._load_record(
                            connection,
                            export_job_id,
                            principal,
                            idempotent_replay=False,
                        )
                    )
                except DomainError as exc:
                    if exc.code != "ANALYTICS_EXPORT_INTEGRITY_ERROR":
                        raise
                    integrity_blocked_job_ids.append(export_job_id)
        return AnalyticsExportPage(
            items=tuple(items),
            total=total,
            page=page,
            page_size=page_size,
            integrity_blocked_job_ids=tuple(integrity_blocked_job_ids),
            integrity_blocked_count=len(integrity_blocked_job_ids),
        )

    def cancel(
        self,
        export_job_id: int,
        request: CancelAnalyticsExportRequest,
        principal: Principal,
    ) -> AnalyticsExportRecord:
        self._require_permissions(principal)
        with self._engine.begin() as connection:
            before = self._load_record(
                connection,
                export_job_id,
                principal,
                idempotent_replay=False,
                hold_lock=True,
            )
            if before.status != "QUEUED":
                raise DomainError(
                    "ANALYTICS_EXPORT_CANCEL_UNSAFE",
                    "only a QUEUED export can be cancelled without Worker coordination",
                    409,
                )
            if before.row_version != request.expected_row_version:
                raise DomainError(
                    "ANALYTICS_EXPORT_WRITE_CONFLICT",
                    "export changed concurrently; refresh before cancelling",
                    409,
                )
            changed = connection.execute(
                text(
                    "UPDATE delivery.export_job SET status='CANCELLED',"
                    "finished_at_utc=SYSUTCDATETIME() OUTPUT INSERTED.export_job_id "
                    "WHERE export_job_id=:export_job_id AND status='QUEUED' "
                    "AND row_version=:expected_row_version"
                ),
                {
                    "export_job_id": export_job_id,
                    "expected_row_version": bytes.fromhex(request.expected_row_version),
                },
            ).scalar_one_or_none()
            if changed is None:
                raise DomainError(
                    "ANALYTICS_EXPORT_WRITE_CONFLICT",
                    "export changed concurrently; refresh before cancelling",
                    409,
                )
            self._audit(
                connection,
                principal=principal,
                operation="ANALYTICS_EXPORT_CANCEL",
                export_job_id=export_job_id,
                before={"row_version": before.row_version, "status": before.status},
                after={"status": "CANCELLED"},
                reason=request.reason,
            )
            return self._load_record(
                connection, export_job_id, principal, idempotent_replay=False
            )

    def download_metadata(
        self, export_job_id: int, principal: Principal
    ) -> AnalyticsExportDownloadMetadata:
        self._require_permissions(principal)
        with self._engine.begin() as connection:
            record = self._load_record(
                connection, export_job_id, principal, idempotent_replay=False
            )
            rows = tuple(
                connection.execute(
                    text(
                        "SELECT export_artifact_id,file_name,mime_type,file_size,sha256,"
                        "created_at_utc,expires_at_utc,physical_status "
                        "FROM delivery.export_artifact "
                        "WHERE export_job_id=:export_job_id ORDER BY export_artifact_id"
                    ),
                    {"export_job_id": export_job_id},
                )
                .mappings()
                .all()
            )
            result = self._download_result(record, rows)
            self._audit(
                connection,
                principal=principal,
                operation="ANALYTICS_EXPORT_DOWNLOAD_METADATA_READ",
                export_job_id=export_job_id,
                before=None,
                after={
                    "artifact_count": len(result.artifacts),
                    "availability": result.availability,
                    "download_enabled": result.download_enabled,
                },
                reason="Read path-free analytics export artifact metadata",
            )
            return result

    def resolve_download(
        self,
        export_job_id: int,
        export_artifact_id: int,
        principal: Principal,
    ) -> AnalyticsExportDownloadTarget:
        """Authorize and verify one managed artifact immediately before streaming."""

        self._require_permissions(principal)
        if export_artifact_id < 1:
            raise DomainError(
                "ANALYTICS_EXPORT_ARTIFACT_NOT_FOUND",
                "analytics export artifact was not found",
                404,
            )
        with self._engine.begin() as connection:
            record = self._load_record(
                connection, export_job_id, principal, idempotent_replay=False
            )
            rows = tuple(
                connection.execute(
                    text(
                        "SELECT export_artifact_id,file_name,mime_type,storage_uri,"
                        "file_size,sha256,created_at_utc,expires_at_utc,physical_status "
                        "FROM delivery.export_artifact WHERE "
                        "export_job_id=:export_job_id ORDER BY export_artifact_id"
                    ),
                    {"export_job_id": export_job_id},
                )
                .mappings()
                .all()
            )
            if (
                len(rows) != 1
                or int(rows[0]["export_artifact_id"]) != export_artifact_id
            ):
                raise DomainError(
                    "ANALYTICS_EXPORT_ARTIFACT_NOT_FOUND",
                    "analytics export artifact was not found",
                    404,
                )
            metadata = self._download_result(record, rows)
            if (
                metadata.availability
                != AnalyticsExportAvailability.ARTIFACT_METADATA_READY.value
            ):
                status_code = (
                    410
                    if metadata.availability
                    == AnalyticsExportAvailability.EXPIRED.value
                    else 409
                )
                raise DomainError(
                    metadata.reason_code,
                    "artifact download is unavailable",
                    status_code,
                )
            row = rows[0]
            try:
                identity = self._path_policy.identify(
                    export_job_id, str(row["storage_uri"])
                )
            except (FileNotFoundError, UnsafeAnalyticsExportPath) as exc:
                raise DomainError(
                    "ANALYTICS_EXPORT_ARTIFACT_INTEGRITY_ERROR",
                    "managed analytics export artifact failed path validation",
                    409,
                ) from exc
            if (
                identity.file_name != str(row["file_name"])
                or identity.file_size != int(row["file_size"])
                or identity.sha256 != str(row["sha256"]).lower()
            ):
                raise DomainError(
                    "ANALYTICS_EXPORT_ARTIFACT_INTEGRITY_ERROR",
                    "managed analytics export artifact failed size or SHA256 validation",
                    409,
                )
            if _utc(row["expires_at_utc"]) <= datetime.now(UTC):
                raise DomainError(
                    "ANALYTICS_EXPORT_ARTIFACT_EXPIRED",
                    "artifact download is unavailable",
                    410,
                )
            self._audit(
                connection,
                principal=principal,
                operation="ANALYTICS_EXPORT_DOWNLOAD",
                export_job_id=export_job_id,
                before=None,
                after={
                    "artifact_id": export_artifact_id,
                    "file_name": identity.file_name,
                    "file_size": identity.file_size,
                    "sha256": identity.sha256,
                },
                reason="Authorized analytics export artifact download",
            )
            return AnalyticsExportDownloadTarget(
                path=identity.path,
                file_name=identity.file_name,
                mime_type=str(row["mime_type"]),
            )

    @staticmethod
    def _download_result(
        record: AnalyticsExportRecord,
        rows: tuple[Mapping[str, Any], ...],
    ) -> AnalyticsExportDownloadMetadata:
        status = AnalyticsExportStatus(record.status)
        if status == AnalyticsExportStatus.EXPIRED:
            return AnalyticsExportDownloadMetadata(
                record.export_job_id,
                status.value,
                AnalyticsExportAvailability.EXPIRED.value,
                False,
                "ANALYTICS_EXPORT_EXPIRED",
                (),
            )
        if status == AnalyticsExportStatus.QUEUED and not rows:
            return AnalyticsExportDownloadMetadata(
                record.export_job_id,
                status.value,
                AnalyticsExportAvailability.PENDING_GENERATION.value,
                False,
                "ANALYTICS_EXPORT_WORKER_REQUIRED",
                (),
            )
        if status == AnalyticsExportStatus.RUNNING and not rows:
            return AnalyticsExportDownloadMetadata(
                record.export_job_id,
                status.value,
                AnalyticsExportAvailability.GENERATING.value,
                False,
                "ANALYTICS_EXPORT_GENERATING",
                (),
            )
        terminal = {
            AnalyticsExportStatus.FAILED: (
                AnalyticsExportAvailability.FAILED,
                "ANALYTICS_EXPORT_WORKER_FAILED",
            ),
            AnalyticsExportStatus.CANCELLED: (
                AnalyticsExportAvailability.CANCELLED,
                "ANALYTICS_EXPORT_CANCELLED",
            ),
        }
        if status in terminal and not rows:
            availability, reason = terminal[status]
            return AnalyticsExportDownloadMetadata(
                record.export_job_id,
                status.value,
                availability.value,
                False,
                reason,
                (),
            )
        if status != AnalyticsExportStatus.SUCCESS or len(rows) != 1:
            return AnalyticsExportDownloadMetadata(
                record.export_job_id,
                status.value,
                AnalyticsExportAvailability.INTEGRITY_BLOCKED.value,
                False,
                "ANALYTICS_EXPORT_ARTIFACT_STATE_INVALID",
                (),
            )

        artifacts: list[AnalyticsExportArtifactMetadata] = []
        now = datetime.now(UTC)
        allowed_mime_types, allowed_suffixes = _ARTIFACT_CONTRACTS[record.export_format]
        for row in rows:
            file_name = str(row["file_name"])
            mime_type = str(row["mime_type"]).lower()
            physical_status = str(row.get("physical_status", "PRESENT"))
            sha256 = str(row["sha256"]).lower()
            try:
                created = _utc(row["created_at_utc"])
                expires = _utc(row["expires_at_utc"])
            except DomainError:
                return AnalyticsExportDownloadMetadata(
                    record.export_job_id,
                    status.value,
                    AnalyticsExportAvailability.INTEGRITY_BLOCKED.value,
                    False,
                    "ANALYTICS_EXPORT_ARTIFACT_INTEGRITY_ERROR",
                    (),
                )
            suffix = "." + file_name.rsplit(".", 1)[-1].lower()
            if (
                not file_name
                or "/" in file_name
                or "\\" in file_name
                or mime_type not in allowed_mime_types
                or suffix not in allowed_suffixes
                or int(row["file_size"]) < 0
                or len(sha256) != 64
                or any(character not in "0123456789abcdef" for character in sha256)
                or expires <= created
                or expires
                > created + timedelta(hours=record.artifact_ttl_hours, minutes=5)
                or physical_status
                not in {"PRESENT", "DELETING", "DELETED", "MISSING", "BLOCKED", "ERROR"}
            ):
                return AnalyticsExportDownloadMetadata(
                    record.export_job_id,
                    status.value,
                    AnalyticsExportAvailability.INTEGRITY_BLOCKED.value,
                    False,
                    "ANALYTICS_EXPORT_ARTIFACT_INTEGRITY_ERROR",
                    (),
                )
            artifacts.append(
                AnalyticsExportArtifactMetadata(
                    export_artifact_id=int(row["export_artifact_id"]),
                    file_name=file_name,
                    mime_type=mime_type,
                    file_size=int(row["file_size"]),
                    sha256=sha256,
                    created_at_utc=created.isoformat(),
                    expires_at_utc=expires.isoformat(),
                )
            )
            if expires <= now:
                return AnalyticsExportDownloadMetadata(
                    record.export_job_id,
                    status.value,
                    AnalyticsExportAvailability.EXPIRED.value,
                    False,
                    "ANALYTICS_EXPORT_ARTIFACT_EXPIRED",
                    tuple(artifacts),
                )
            if physical_status != "PRESENT":
                return AnalyticsExportDownloadMetadata(
                    record.export_job_id,
                    status.value,
                    AnalyticsExportAvailability.INTEGRITY_BLOCKED.value,
                    False,
                    "ANALYTICS_EXPORT_ARTIFACT_PHYSICAL_STATE_INVALID",
                    tuple(artifacts),
                )
        return AnalyticsExportDownloadMetadata(
            record.export_job_id,
            status.value,
            AnalyticsExportAvailability.ARTIFACT_METADATA_READY.value,
            True,
            "ANALYTICS_EXPORT_READY",
            tuple(artifacts),
        )
