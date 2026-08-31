from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from app.core.errors import DomainError
from app.domain.analysis_rule_pinning import (
    AnalysisRuleRequirement,
    required_rules_from_analysis_view_state,
)
from app.domain.analytics import (
    AnalyticsContextRequest,
    AnalyticsDatasetReference,
    AnalyticsFilters,
)
from app.domain.auth import Principal
from app.domain.saved_analyses import (
    CreateSavedAnalysisRequest,
    CreateSavedAnalysisRevisionRequest,
    DeleteSavedAnalysisRequest,
    SavedAnalysisDatasetRecord,
    SavedAnalysisDatasetStatus,
    SavedAnalysisPage,
    SavedAnalysisRecord,
    SavedAnalysisRestoreStatus,
    SavedAnalysisRevisionRecord,
    SavedAnalysisRuleContext,
    SavedAnalysisState,
    canonical_json,
    saved_analysis_hashes,
)
from app.infrastructure.analysis_rule_pinning import (
    ApprovedRuleParameterResolver,
    validate_required_analysis_rules,
    verified_merged_rule_context,
)
from app.infrastructure.formal_spec_context_resolver import (
    resolve_formal_spec_context,
)
from app.infrastructure.sql_analysis_rule_service import SqlAnalysisRuleService

RuleContextResolver = Callable[
    [Connection, tuple[Mapping[str, Any], ...], AnalyticsContextRequest],
    SavedAnalysisRuleContext,
]


def _is_admin(principal: Principal) -> bool:
    return "SYSTEM_ADMIN" in principal.roles


def _row_version_hex(value: Any) -> str:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        if len(value) != 8:
            raise DomainError(
                "SAVED_ANALYSIS_INTEGRITY_ERROR",
                "saved analysis row version has an invalid database representation",
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
            "SAVED_ANALYSIS_INTEGRITY_ERROR",
            "saved analysis row version has an invalid database representation",
            409,
        )
    return rendered


def _row_version_bytes(value: str) -> bytes:
    return bytes.fromhex(value)


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _mapping_one(result) -> Mapping[str, Any]:
    row = result.mappings().one_or_none()
    if row is None:
        raise DomainError(
            "SAVED_ANALYSIS_WRITE_CONFLICT",
            "saved analysis changed concurrently; refresh before retrying",
            409,
        )
    return row


class SqlSavedAnalysisService:
    """Transactional Saved Analysis storage over sql2014_0020."""

    def __init__(
        self,
        engine: Engine,
        *,
        rule_context_resolver: RuleContextResolver | None = None,
        approved_rule_resolver: ApprovedRuleParameterResolver | None = None,
    ) -> None:
        self._engine = engine
        self._rule_context_resolver = (
            rule_context_resolver or self._default_rule_context
        )
        self._approved_rule_resolver = (
            approved_rule_resolver
            or SqlAnalysisRuleService(engine).approved_rule_parameters
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
                "SAVED_ANALYSIS_DATASET_NOT_FOUND",
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
                    "SAVED_ANALYSIS_DATASET_ACCESS_DENIED",
                    "one selected Dataset Version is outside the user's access scope",
                    403,
                )
            if require_current and (
                str(row["status"]) != "PUBLISHED" or not bool(row["is_current"])
            ):
                raise DomainError(
                    "SAVED_ANALYSIS_DATASET_NOT_CURRENT",
                    "Saved Analysis accepts only exact Current Published Dataset Versions",
                    409,
                )
        stages = {str(row["test_stage"]) for row in rows}
        if len(stages) != 1 or not stages.issubset({"CP", "FT"}):
            raise DomainError(
                "SAVED_ANALYSIS_STAGE_INCOMPATIBLE",
                "one Saved Analysis may contain only CP or only FT Dataset Versions",
                409,
            )
        if len(rows) > 1 and next(iter(stages)) == "CP":
            spec_ids = {row["spec_set_id"] for row in rows}
            if None in spec_ids or len(spec_ids) != 1:
                raise DomainError(
                    "SAVED_ANALYSIS_SPEC_INCOMPATIBLE",
                    "selected CP Dataset Versions do not have one proven compatible Spec",
                    409,
                )
        return rows

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
            values = (
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
                for item in values
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
            stale_code="SAVED_ANALYSIS_RULE_CONTEXT_STALE",
            stale_message=(
                "rule context changed before the Saved Analysis revision was written"
            ),
        )

    @staticmethod
    def _required_rules(
        chart_config: dict[str, Any], parameters: Sequence[str]
    ) -> tuple[AnalysisRuleRequirement, ...]:
        try:
            return required_rules_from_analysis_view_state(
                chart_config, tuple(parameters)
            )
        except ValueError as exc:
            raise DomainError(
                "SAVED_ANALYSIS_VIEW_CONFIG_INVALID",
                "versioned analysis view state is invalid or has incomplete exact Rule references",
                409,
            ) from exc

    @staticmethod
    def _serialized_state(
        request: SavedAnalysisState,
        rule_context: SavedAnalysisRuleContext,
    ) -> tuple[str, str, str, Any]:
        hashes = saved_analysis_hashes(request)
        filter_json = canonical_json(
            {
                "filters": hashes.normalized_filters,
                "parameters": list(hashes.normalized_parameters),
            }
        )
        rule_json = canonical_json(rule_context.model_dump(mode="json"))
        chart_json = canonical_json(
            {
                "chart_config": request.chart_config,
                "display_config": request.display_config,
            }
        )
        return filter_json, rule_json, chart_json, hashes

    @staticmethod
    def _insert_revision(
        connection: Connection,
        *,
        saved_analysis_id: int,
        revision_no: int,
        request: SavedAnalysisState,
        actor_user_id: int,
        filter_json: str,
        rule_json: str,
        chart_json: str,
        filter_hash: str,
        context_hash: str,
        dataset_rows: tuple[Mapping[str, Any], ...],
    ) -> Mapping[str, Any]:
        revision = _mapping_one(
            connection.execute(
                text(
                    "INSERT analysis.saved_analysis_revision("
                    "saved_analysis_id,revision_no,contract_version,filter_json,"
                    "filter_hash,context_hash,rule_context_json,chart_config_json,"
                    "created_by_user_id) OUTPUT INSERTED.saved_analysis_revision_id,"
                    "INSERTED.created_at_utc VALUES(:saved_analysis_id,:revision_no,"
                    ":contract_version,:filter_json,:filter_hash,:context_hash,"
                    ":rule_context_json,:chart_config_json,:created_by_user_id)"
                ),
                {
                    "saved_analysis_id": saved_analysis_id,
                    "revision_no": revision_no,
                    "contract_version": request.contract_version,
                    "filter_json": filter_json,
                    "filter_hash": filter_hash,
                    "context_hash": context_hash,
                    "rule_context_json": rule_json,
                    "chart_config_json": chart_json,
                    "created_by_user_id": actor_user_id,
                },
            )
        )
        revision_id = int(revision["saved_analysis_revision_id"])
        for ordinal, row in enumerate(dataset_rows, start=1):
            connection.execute(
                text(
                    "INSERT analysis.saved_analysis_revision_dataset("
                    "saved_analysis_revision_id,dataset_version_id,ordinal_no) "
                    "VALUES(:saved_analysis_revision_id,:dataset_version_id,:ordinal_no)"
                ),
                {
                    "saved_analysis_revision_id": revision_id,
                    "dataset_version_id": int(row["dataset_version_id"]),
                    "ordinal_no": ordinal,
                },
            )
        return revision

    @staticmethod
    def _audit(
        connection: Connection,
        *,
        principal: Principal,
        operation: str,
        saved_analysis_id: int,
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any] | None,
        reason: str,
    ) -> None:
        connection.execute(
            text(
                "INSERT governance.audit_log(actor,actor_user_id,operation,entity_type,"
                "entity_id,before_json,after_json,reason) VALUES(:actor,:actor_user_id,"
                ":operation,'SAVED_ANALYSIS',:entity_id,:before_json,:after_json,:reason)"
            ),
            {
                "actor": principal.login_name,
                "actor_user_id": principal.user_id,
                "operation": operation,
                "entity_id": str(saved_analysis_id),
                "before_json": canonical_json(before) if before is not None else None,
                "after_json": canonical_json(after) if after is not None else None,
                "reason": reason,
            },
        )

    @staticmethod
    def _written_revision_record(
        *,
        revision: Mapping[str, Any],
        revision_no: int,
        request: SavedAnalysisState,
        actor_user_id: int,
        dataset_rows: tuple[Mapping[str, Any], ...],
        rule_context: SavedAnalysisRuleContext,
        filter_hash: str,
        context_hash: str,
    ) -> SavedAnalysisRevisionRecord:
        hashes = saved_analysis_hashes(request)
        datasets = tuple(
            SavedAnalysisDatasetRecord(
                dataset_version_id=int(row["dataset_version_id"]),
                dataset_id=int(row["dataset_id"]),
                version_no=int(row["version_no"]),
                ordinal_no=ordinal,
                test_stage=str(row["test_stage"]),
                status=SavedAnalysisDatasetStatus.CURRENT,
            )
            for ordinal, row in enumerate(dataset_rows, start=1)
        )
        return SavedAnalysisRevisionRecord(
            saved_analysis_revision_id=int(revision["saved_analysis_revision_id"]),
            revision_no=revision_no,
            contract_version=request.contract_version,
            filters=hashes.normalized_filters,
            parameters=hashes.normalized_parameters,
            filter_hash=filter_hash,
            context_hash=context_hash,
            rule_context=rule_context,
            chart_config=request.chart_config,
            display_config=request.display_config,
            datasets=datasets,
            created_by_user_id=actor_user_id,
            created_at_utc=_timestamp(revision["created_at_utc"]),
        )

    def create(
        self, request: CreateSavedAnalysisRequest, principal: Principal
    ) -> SavedAnalysisRecord:
        with self._engine.begin() as connection:
            dataset_rows = self._dataset_rows(
                connection,
                request.datasets,
                principal,
                require_current=True,
                hold_lock=True,
            )
            required_rules = self._required_rules(
                request.chart_config, request.parameters
            )
            rule_context = self._verified_rule_context(
                connection, dataset_rows, request, request.rule_context, required_rules
            )
            filter_json, rule_json, chart_json, hashes = self._serialized_state(
                request, rule_context
            )
            root = _mapping_one(
                connection.execute(
                    text(
                        "INSERT analysis.saved_analysis(owner_user_id,dataset_version_id,"
                        "analysis_name,filter_json,chart_config_json,"
                        "evaluation_context_json,contract_version,filter_hash,context_hash,"
                        "current_revision_no,lifecycle_status) OUTPUT "
                        "INSERTED.saved_analysis_id,INSERTED.row_version,"
                        "INSERTED.created_at_utc,INSERTED.updated_at_utc VALUES("
                        ":owner_user_id,:dataset_version_id,:analysis_name,:filter_json,"
                        ":chart_config_json,:evaluation_context_json,:contract_version,"
                        ":filter_hash,:context_hash,1,'ACTIVE')"
                    ),
                    {
                        "owner_user_id": principal.user_id,
                        "dataset_version_id": int(
                            dataset_rows[0]["dataset_version_id"]
                        ),
                        "analysis_name": request.analysis_name,
                        "filter_json": filter_json,
                        "chart_config_json": chart_json,
                        "evaluation_context_json": rule_json,
                        "contract_version": request.contract_version,
                        "filter_hash": hashes.filter_hash,
                        "context_hash": hashes.context_hash,
                    },
                )
            )
            saved_analysis_id = int(root["saved_analysis_id"])
            revision = self._insert_revision(
                connection,
                saved_analysis_id=saved_analysis_id,
                revision_no=1,
                request=request,
                actor_user_id=principal.user_id,
                filter_json=filter_json,
                rule_json=rule_json,
                chart_json=chart_json,
                filter_hash=hashes.filter_hash,
                context_hash=hashes.context_hash,
                dataset_rows=dataset_rows,
            )
            row_version = _row_version_hex(root["row_version"])
            self._audit(
                connection,
                principal=principal,
                operation="SAVED_ANALYSIS_CREATE",
                saved_analysis_id=saved_analysis_id,
                before=None,
                after={
                    "revision_no": 1,
                    "dataset_version_ids": [
                        int(row["dataset_version_id"]) for row in dataset_rows
                    ],
                    "filter_hash": hashes.filter_hash,
                    "context_hash": hashes.context_hash,
                    "row_version": row_version,
                },
                reason=request.change_reason,
            )
            revision_record = self._written_revision_record(
                revision=revision,
                revision_no=1,
                request=request,
                actor_user_id=principal.user_id,
                dataset_rows=dataset_rows,
                rule_context=rule_context,
                filter_hash=hashes.filter_hash,
                context_hash=hashes.context_hash,
            )
            return SavedAnalysisRecord(
                saved_analysis_id=saved_analysis_id,
                analysis_name=request.analysis_name,
                owner_user_id=principal.user_id,
                lifecycle_status="ACTIVE",
                current_revision_no=1,
                row_version=row_version,
                restore_status=SavedAnalysisRestoreStatus.CURRENT,
                revision=revision_record,
                created_at_utc=_timestamp(root["created_at_utc"]),
                updated_at_utc=_timestamp(root["updated_at_utc"]),
            )

    @staticmethod
    def _root_row(
        connection: Connection, saved_analysis_id: int, *, lock: bool
    ) -> Mapping[str, Any]:
        lock_hint = " WITH (UPDLOCK,HOLDLOCK)" if lock else ""
        row = (
            connection.execute(
                text(
                    "SELECT sa.saved_analysis_id,sa.owner_user_id,sa.analysis_name,"
                    "sa.contract_version,sa.filter_hash,sa.context_hash,"
                    "sa.current_revision_no,sa.lifecycle_status,sa.row_version,"
                    "sa.created_at_utc,sa.updated_at_utc "
                    "FROM analysis.saved_analysis sa"
                    + lock_hint
                    + " WHERE sa.saved_analysis_id=:saved_analysis_id"
                ),
                {"saved_analysis_id": saved_analysis_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DomainError(
                "SAVED_ANALYSIS_NOT_FOUND", "Saved Analysis was not found", 404
            )
        return row

    @staticmethod
    def _assert_manage(root: Mapping[str, Any], principal: Principal) -> None:
        if not _is_admin(principal) and int(root["owner_user_id"]) != principal.user_id:
            raise DomainError(
                "SAVED_ANALYSIS_OWNER_REQUIRED",
                "only the Saved Analysis owner or a System Admin may change it",
                403,
            )

    @staticmethod
    def _assert_active(root: Mapping[str, Any]) -> None:
        if str(root["lifecycle_status"]) != "ACTIVE":
            raise DomainError(
                "SAVED_ANALYSIS_DELETED",
                "deleted Saved Analysis records are immutable",
                409,
            )

    @staticmethod
    def _assert_row_version(root: Mapping[str, Any], expected: str) -> None:
        if _row_version_hex(root["row_version"]) != expected:
            raise DomainError(
                "SAVED_ANALYSIS_ROW_VERSION_CONFLICT",
                "Saved Analysis changed concurrently; refresh before retrying",
                409,
            )

    def create_revision(
        self,
        saved_analysis_id: int,
        request: CreateSavedAnalysisRevisionRequest,
        principal: Principal,
    ) -> SavedAnalysisRecord:
        try:
            with self._engine.begin() as connection:
                root = self._root_row(connection, saved_analysis_id, lock=True)
                self._assert_manage(root, principal)
                self._assert_active(root)
                self._assert_row_version(root, request.expected_row_version)
                dataset_rows = self._dataset_rows(
                    connection,
                    request.datasets,
                    principal,
                    require_current=True,
                    hold_lock=True,
                )
                required_rules = self._required_rules(
                    request.chart_config, request.parameters
                )
                rule_context = self._verified_rule_context(
                    connection,
                    dataset_rows,
                    request,
                    request.rule_context,
                    required_rules,
                )
                filter_json, rule_json, chart_json, hashes = self._serialized_state(
                    request, rule_context
                )
                revision_no = int(root["current_revision_no"]) + 1
                revision = self._insert_revision(
                    connection,
                    saved_analysis_id=saved_analysis_id,
                    revision_no=revision_no,
                    request=request,
                    actor_user_id=principal.user_id,
                    filter_json=filter_json,
                    rule_json=rule_json,
                    chart_json=chart_json,
                    filter_hash=hashes.filter_hash,
                    context_hash=hashes.context_hash,
                    dataset_rows=dataset_rows,
                )
                name = request.analysis_name or str(root["analysis_name"])
                updated = _mapping_one(
                    connection.execute(
                        text(
                            "UPDATE analysis.saved_analysis SET analysis_name=:analysis_name,"
                            "dataset_version_id=:dataset_version_id,filter_json=:filter_json,"
                            "chart_config_json=:chart_config_json,"
                            "evaluation_context_json=:evaluation_context_json,"
                            "contract_version=:contract_version,filter_hash=:filter_hash,"
                            "context_hash=:context_hash,current_revision_no=:revision_no,"
                            "updated_at_utc=SYSUTCDATETIME() OUTPUT INSERTED.row_version,"
                            "INSERTED.updated_at_utc WHERE saved_analysis_id=:saved_analysis_id "
                            "AND row_version=:expected_row_version"
                        ),
                        {
                            "analysis_name": name,
                            "dataset_version_id": int(
                                dataset_rows[0]["dataset_version_id"]
                            ),
                            "filter_json": filter_json,
                            "chart_config_json": chart_json,
                            "evaluation_context_json": rule_json,
                            "contract_version": request.contract_version,
                            "filter_hash": hashes.filter_hash,
                            "context_hash": hashes.context_hash,
                            "revision_no": revision_no,
                            "saved_analysis_id": saved_analysis_id,
                            "expected_row_version": _row_version_bytes(
                                request.expected_row_version
                            ),
                        },
                    )
                )
                new_row_version = _row_version_hex(updated["row_version"])
                self._audit(
                    connection,
                    principal=principal,
                    operation="SAVED_ANALYSIS_REVISE",
                    saved_analysis_id=saved_analysis_id,
                    before={
                        "revision_no": int(root["current_revision_no"]),
                        "filter_hash": str(root["filter_hash"] or ""),
                        "context_hash": str(root["context_hash"] or ""),
                        "row_version": request.expected_row_version,
                    },
                    after={
                        "revision_no": revision_no,
                        "dataset_version_ids": [
                            int(row["dataset_version_id"]) for row in dataset_rows
                        ],
                        "filter_hash": hashes.filter_hash,
                        "context_hash": hashes.context_hash,
                        "row_version": new_row_version,
                    },
                    reason=request.change_reason,
                )
                revision_record = self._written_revision_record(
                    revision=revision,
                    revision_no=revision_no,
                    request=request,
                    actor_user_id=principal.user_id,
                    dataset_rows=dataset_rows,
                    rule_context=rule_context,
                    filter_hash=hashes.filter_hash,
                    context_hash=hashes.context_hash,
                )
                return SavedAnalysisRecord(
                    saved_analysis_id=saved_analysis_id,
                    analysis_name=name,
                    owner_user_id=int(root["owner_user_id"]),
                    lifecycle_status="ACTIVE",
                    current_revision_no=revision_no,
                    row_version=new_row_version,
                    restore_status=SavedAnalysisRestoreStatus.CURRENT,
                    revision=revision_record,
                    created_at_utc=_timestamp(root["created_at_utc"]),
                    updated_at_utc=_timestamp(updated["updated_at_utc"]),
                )
        except IntegrityError as exc:
            raise DomainError(
                "SAVED_ANALYSIS_REVISION_CONFLICT",
                "Saved Analysis revision number changed concurrently",
                409,
            ) from exc

    @staticmethod
    def _revision_row(
        connection: Connection,
        saved_analysis_id: int,
        revision_no: int,
    ) -> Mapping[str, Any]:
        row = (
            connection.execute(
                text(
                    "SELECT sar.saved_analysis_revision_id,sar.revision_no,"
                    "sar.contract_version,sar.filter_json,sar.filter_hash,"
                    "sar.context_hash,sar.rule_context_json,sar.chart_config_json,"
                    "sar.created_by_user_id,sar.created_at_utc "
                    "FROM analysis.saved_analysis_revision sar "
                    "WHERE sar.saved_analysis_id=:saved_analysis_id "
                    "AND sar.revision_no=:revision_no"
                ),
                {
                    "saved_analysis_id": saved_analysis_id,
                    "revision_no": revision_no,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DomainError(
                "SAVED_ANALYSIS_REVISION_NOT_FOUND",
                "Saved Analysis revision was not found",
                404,
            )
        return row

    @staticmethod
    def _revision_dataset_rows(
        connection: Connection, saved_analysis_revision_id: int
    ) -> tuple[Mapping[str, Any], ...]:
        rows = tuple(
            connection.execute(
                text(
                    "SELECT sard.dataset_version_id,sard.ordinal_no,dv.dataset_id,"
                    "dv.version_no,dv.status,dv.is_current,dv.spec_set_id,d.test_stage,"
                    "d.owner_user_id,d.supplier_id,d.product_id,b.business_domain,"
                    "ss.version_code AS spec_version "
                    "FROM analysis.saved_analysis_revision_dataset sard "
                    "JOIN dataset.dataset_version dv "
                    "ON dv.dataset_version_id=sard.dataset_version_id "
                    "JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                    "LEFT JOIN ingestion.import_batch b "
                    "ON b.import_batch_id=dv.input_batch_id "
                    "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=dv.spec_set_id "
                    "WHERE sard.saved_analysis_revision_id=:revision_id "
                    "ORDER BY sard.ordinal_no"
                ),
                {"revision_id": saved_analysis_revision_id},
            )
            .mappings()
            .all()
        )
        if not 1 <= len(rows) <= 8:
            raise DomainError(
                "SAVED_ANALYSIS_INTEGRITY_ERROR",
                "Saved Analysis revision has an invalid Dataset Version count",
                409,
            )
        if [int(row["ordinal_no"]) for row in rows] != list(range(1, len(rows) + 1)):
            raise DomainError(
                "SAVED_ANALYSIS_INTEGRITY_ERROR",
                "Saved Analysis Dataset Version ordinals are invalid",
                409,
            )
        return rows

    @staticmethod
    def _decode_revision_state(
        revision: Mapping[str, Any], dataset_rows: tuple[Mapping[str, Any], ...]
    ) -> tuple[
        AnalyticsContextRequest,
        SavedAnalysisRuleContext,
        dict[str, Any],
        dict[str, Any],
    ]:
        try:
            filter_state = json.loads(str(revision["filter_json"]))
            chart_state = json.loads(str(revision["chart_config_json"]))
            rule_context = SavedAnalysisRuleContext.model_validate_json(
                str(revision["rule_context_json"])
            )
            if not isinstance(filter_state, dict) or set(filter_state) != {
                "filters",
                "parameters",
            }:
                raise ValueError("invalid filter state")
            if not isinstance(chart_state, dict) or set(chart_state) != {
                "chart_config",
                "display_config",
            }:
                raise ValueError("invalid chart state")
            context = AnalyticsContextRequest(
                datasets=[
                    AnalyticsDatasetReference(
                        dataset_id=int(row["dataset_id"]),
                        version_no=int(row["version_no"]),
                    )
                    for row in dataset_rows
                ],
                filters=AnalyticsFilters.model_validate(filter_state["filters"]),
                parameters=filter_state["parameters"],
            )
            state = SavedAnalysisState(
                contract_version=str(revision["contract_version"]),
                datasets=context.datasets,
                filters=context.filters,
                parameters=context.parameters,
                rule_context=rule_context,
                chart_config=chart_state["chart_config"],
                display_config=chart_state["display_config"],
            )
        except (TypeError, ValueError) as exc:
            raise DomainError(
                "SAVED_ANALYSIS_INTEGRITY_ERROR",
                "Saved Analysis revision contains invalid persisted JSON",
                409,
            ) from exc
        return (
            context,
            rule_context,
            state.chart_config,
            state.display_config,
        )

    def _read_record(
        self,
        connection: Connection,
        root: Mapping[str, Any],
        principal: Principal,
        *,
        revision_no: int,
    ) -> SavedAnalysisRecord:
        revision = self._revision_row(
            connection, int(root["saved_analysis_id"]), revision_no
        )
        dataset_rows = self._revision_dataset_rows(
            connection, int(revision["saved_analysis_revision_id"])
        )
        dataset_records: list[SavedAnalysisDatasetRecord] = []
        any_access_revoked = False
        any_non_current = False
        for row in dataset_rows:
            if not self._can_read_dataset(row, principal):
                status = SavedAnalysisDatasetStatus.ACCESS_REVOKED
                any_access_revoked = True
            elif str(row["status"]) != "PUBLISHED" or not bool(row["is_current"]):
                status = SavedAnalysisDatasetStatus.NON_CURRENT
                any_non_current = True
            else:
                status = SavedAnalysisDatasetStatus.CURRENT
            dataset_records.append(
                SavedAnalysisDatasetRecord(
                    dataset_version_id=int(row["dataset_version_id"]),
                    dataset_id=int(row["dataset_id"]),
                    version_no=int(row["version_no"]),
                    ordinal_no=int(row["ordinal_no"]),
                    test_stage=str(row["test_stage"]),
                    status=status,
                )
            )

        owns_saved = int(root["owner_user_id"]) == principal.user_id
        if any_access_revoked and not owns_saved and not _is_admin(principal):
            raise DomainError(
                "SAVED_ANALYSIS_NOT_FOUND",
                "Saved Analysis was not found or is outside the user's access scope",
                404,
            )
        if str(root["lifecycle_status"]) == "DELETED" and not (
            owns_saved or _is_admin(principal)
        ):
            raise DomainError(
                "SAVED_ANALYSIS_NOT_FOUND", "Saved Analysis was not found", 404
            )

        context, saved_rules, chart_config, display_config = (
            self._decode_revision_state(revision, dataset_rows)
        )
        hashes = saved_analysis_hashes(context)
        if hashes.filter_hash != str(
            revision["filter_hash"]
        ) or hashes.context_hash != str(revision["context_hash"]):
            raise DomainError(
                "SAVED_ANALYSIS_INTEGRITY_ERROR",
                "Saved Analysis hashes do not match the persisted revision",
                409,
            )
        if revision_no == int(root["current_revision_no"]) and (
            str(root["contract_version"]) != str(revision["contract_version"])
            or str(root["filter_hash"] or "") != str(revision["filter_hash"])
            or str(root["context_hash"] or "") != str(revision["context_hash"])
        ):
            raise DomainError(
                "SAVED_ANALYSIS_INTEGRITY_ERROR",
                "Saved Analysis root and current revision are inconsistent",
                409,
            )

        rule_changed = False
        if not any_access_revoked:
            current_rules = self._rule_context_resolver(
                connection, dataset_rows, context
            )
            try:
                required_rules = self._required_rules(chart_config, context.parameters)
            except DomainError as exc:
                raise DomainError(
                    "SAVED_ANALYSIS_INTEGRITY_ERROR",
                    "Saved Analysis contains an invalid versioned analysis view state",
                    409,
                ) from exc
            try:
                required_identities = validate_required_analysis_rules(
                    required_rules, dataset_rows, self._approved_rule_resolver
                )
                effective_rules = verified_merged_rule_context(
                    current=current_rules,
                    requested=saved_rules,
                    required_identities=required_identities,
                    stale_code="SAVED_ANALYSIS_RULE_CONTEXT_STALE",
                    stale_message="Saved Analysis Rule context is no longer current",
                )
                rule_changed = effective_rules.model_dump(
                    mode="json"
                ) != saved_rules.model_dump(mode="json")
            except DomainError:
                # A revoked approval, disabled activation, scope mismatch or base
                # context change makes restore stale. The immutable revision stays
                # readable, but callers must not execute it as CURRENT.
                rule_changed = True
        if any_access_revoked:
            restore_status = SavedAnalysisRestoreStatus.ACCESS_REVOKED
        elif any_non_current:
            restore_status = SavedAnalysisRestoreStatus.NON_CURRENT
        elif rule_changed:
            restore_status = SavedAnalysisRestoreStatus.RULE_CHANGED
        else:
            restore_status = SavedAnalysisRestoreStatus.CURRENT

        revision_record = SavedAnalysisRevisionRecord(
            saved_analysis_revision_id=int(revision["saved_analysis_revision_id"]),
            revision_no=int(revision["revision_no"]),
            contract_version=str(revision["contract_version"]),
            filters=hashes.normalized_filters,
            parameters=hashes.normalized_parameters,
            filter_hash=hashes.filter_hash,
            context_hash=hashes.context_hash,
            rule_context=saved_rules,
            chart_config=chart_config,
            display_config=display_config,
            datasets=tuple(dataset_records),
            created_by_user_id=int(revision["created_by_user_id"]),
            created_at_utc=_timestamp(revision["created_at_utc"]),
        )
        return SavedAnalysisRecord(
            saved_analysis_id=int(root["saved_analysis_id"]),
            analysis_name=str(root["analysis_name"]),
            owner_user_id=int(root["owner_user_id"]),
            lifecycle_status=str(root["lifecycle_status"]),
            current_revision_no=int(root["current_revision_no"]),
            row_version=_row_version_hex(root["row_version"]),
            restore_status=restore_status,
            revision=revision_record,
            created_at_utc=_timestamp(root["created_at_utc"]),
            updated_at_utc=_timestamp(root["updated_at_utc"]),
        )

    def get(
        self,
        saved_analysis_id: int,
        principal: Principal,
        *,
        revision_no: int | None = None,
    ) -> SavedAnalysisRecord:
        with self._engine.connect() as connection:
            root = self._root_row(connection, saved_analysis_id, lock=False)
            selected_revision = revision_no or int(root["current_revision_no"])
            return self._read_record(
                connection, root, principal, revision_no=selected_revision
            )

    @staticmethod
    def _list_scope_sql() -> str:
        return (
            "(:is_admin=1 OR sa.owner_user_id=:user_id OR ("
            "EXISTS(SELECT 1 FROM analysis.saved_analysis_revision visible_sar "
            "JOIN analysis.saved_analysis_revision_dataset visible_sard "
            "ON visible_sard.saved_analysis_revision_id="
            "visible_sar.saved_analysis_revision_id "
            "WHERE visible_sar.saved_analysis_id=sa.saved_analysis_id "
            "AND visible_sar.revision_no=sa.current_revision_no) AND "
            "NOT EXISTS(SELECT 1 FROM analysis.saved_analysis_revision denied_sar "
            "JOIN analysis.saved_analysis_revision_dataset denied_sard "
            "ON denied_sard.saved_analysis_revision_id="
            "denied_sar.saved_analysis_revision_id "
            "JOIN dataset.dataset_version denied_dv "
            "ON denied_dv.dataset_version_id=denied_sard.dataset_version_id "
            "JOIN dataset.dataset denied_d ON denied_d.dataset_id=denied_dv.dataset_id "
            "LEFT JOIN ingestion.import_batch denied_b "
            "ON denied_b.import_batch_id=denied_dv.input_batch_id "
            "WHERE denied_sar.saved_analysis_id=sa.saved_analysis_id "
            "AND denied_sar.revision_no=sa.current_revision_no AND NOT("
            "denied_d.owner_user_id=:user_id OR ("
            "denied_b.business_domain='PRODUCTION' AND "
            "denied_dv.status='PUBLISHED' AND denied_dv.is_current=1)))))"
        )

    def list_page(
        self,
        principal: Principal,
        *,
        page: int,
        page_size: int,
        include_deleted: bool = False,
    ) -> SavedAnalysisPage:
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("Saved Analysis page and page_size are out of bounds")
        if include_deleted and not _is_admin(principal):
            raise DomainError(
                "SAVED_ANALYSIS_ADMIN_REQUIRED",
                "only a System Admin may list logically deleted Saved Analyses",
                403,
            )
        lifecycle_sql = "" if include_deleted else "sa.lifecycle_status='ACTIVE' AND "
        revision_exists = (
            "EXISTS(SELECT 1 FROM analysis.saved_analysis_revision current_sar "
            "WHERE current_sar.saved_analysis_id=sa.saved_analysis_id "
            "AND current_sar.revision_no=sa.current_revision_no)"
        )
        where_sql = (
            " WHERE "
            + lifecycle_sql
            + revision_exists
            + " AND "
            + self._list_scope_sql()
        )
        parameters = {
            "is_admin": int(_is_admin(principal)),
            "user_id": principal.user_id,
            "offset": (page - 1) * page_size,
            "page_size": page_size,
        }
        with self._engine.connect() as connection:
            total = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM analysis.saved_analysis sa"
                        + where_sql
                    ),
                    parameters,
                ).scalar_one()
            )
            root_rows = tuple(
                connection.execute(
                    text(
                        "SELECT sa.saved_analysis_id,sa.owner_user_id,sa.analysis_name,"
                        "sa.contract_version,sa.filter_hash,sa.context_hash,"
                        "sa.current_revision_no,sa.lifecycle_status,sa.row_version,"
                        "sa.created_at_utc,sa.updated_at_utc "
                        "FROM analysis.saved_analysis sa"
                        + where_sql
                        + " ORDER BY sa.updated_at_utc DESC,sa.saved_analysis_id DESC "
                        "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
            items = tuple(
                self._read_record(
                    connection,
                    root,
                    principal,
                    revision_no=int(root["current_revision_no"]),
                )
                for root in root_rows
            )
        return SavedAnalysisPage(
            items=items, total=total, page=page, page_size=page_size
        )

    def delete(
        self,
        saved_analysis_id: int,
        request: DeleteSavedAnalysisRequest,
        principal: Principal,
    ) -> SavedAnalysisRecord:
        with self._engine.begin() as connection:
            root = self._root_row(connection, saved_analysis_id, lock=True)
            self._assert_manage(root, principal)
            self._assert_active(root)
            self._assert_row_version(root, request.expected_row_version)
            updated = _mapping_one(
                connection.execute(
                    text(
                        "UPDATE analysis.saved_analysis SET lifecycle_status='DELETED',"
                        "updated_at_utc=SYSUTCDATETIME() OUTPUT INSERTED.row_version,"
                        "INSERTED.updated_at_utc WHERE saved_analysis_id=:saved_analysis_id "
                        "AND row_version=:expected_row_version AND lifecycle_status='ACTIVE'"
                    ),
                    {
                        "saved_analysis_id": saved_analysis_id,
                        "expected_row_version": _row_version_bytes(
                            request.expected_row_version
                        ),
                    },
                )
            )
            new_row_version = _row_version_hex(updated["row_version"])
            self._audit(
                connection,
                principal=principal,
                operation="SAVED_ANALYSIS_DELETE",
                saved_analysis_id=saved_analysis_id,
                before={
                    "lifecycle_status": "ACTIVE",
                    "revision_no": int(root["current_revision_no"]),
                    "row_version": request.expected_row_version,
                },
                after={
                    "lifecycle_status": "DELETED",
                    "revision_no": int(root["current_revision_no"]),
                    "row_version": new_row_version,
                },
                reason=request.reason,
            )
            deleted_root = dict(root)
            deleted_root.update(
                {
                    "lifecycle_status": "DELETED",
                    "row_version": updated["row_version"],
                    "updated_at_utc": updated["updated_at_utc"],
                }
            )
            return self._read_record(
                connection,
                deleted_root,
                principal,
                revision_no=int(root["current_revision_no"]),
            )
