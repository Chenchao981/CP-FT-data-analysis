from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from app.core.errors import DomainError
from app.domain.analysis_rules import (
    ActivateAnalysisRuleRequest,
    AnalysisRuleActivationRecord,
    AnalysisRuleSetRecord,
    AnalysisRuleVersionRecord,
    CreateAnalysisRuleSetRequest,
    CreateAnalysisRuleVersionRequest,
    DecideAnalysisRuleRequest,
    RuleApprovalDecision,
    RuleApprovalRole,
)
from app.domain.auth import Principal


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pattern_matches(pattern: str, parameter: str) -> bool:
    return (
        parameter.startswith(pattern[:-1])
        if pattern.endswith("*")
        else parameter == pattern
    )


def _pattern_overlaps(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return True
    left_prefix = left.removesuffix("*")
    right_prefix = right.removesuffix("*")
    if left.endswith("*") and right.endswith("*"):
        return left_prefix.startswith(right_prefix) or right_prefix.startswith(
            left_prefix
        )
    if left.endswith("*"):
        return right.startswith(left_prefix)
    if right.endswith("*"):
        return left.startswith(right_prefix)
    return left == right


class SqlAnalysisRuleService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _require_govern(principal: Principal) -> None:
        if not principal.can("RULE_GOVERN"):
            raise DomainError("PERMISSION_DENIED", "缺少权限：RULE_GOVERN", 403)

    @staticmethod
    def _audit(
        connection: Connection,
        principal: Principal,
        operation: str,
        entity_type: str,
        entity_id: int,
        *,
        before: object | None,
        after: object | None,
        reason: str,
    ) -> None:
        connection.execute(
            text(
                "INSERT governance.audit_log(actor,actor_user_id,operation,"
                "entity_type,entity_id,before_json,after_json,reason) VALUES("
                ":actor,:actor_id,:operation,:entity_type,:entity_id,"
                ":before_json,:after_json,:reason)"
            ),
            {
                "actor": principal.login_name,
                "actor_id": principal.user_id,
                "operation": operation,
                "entity_type": entity_type,
                "entity_id": str(entity_id),
                "before_json": _json(before) if before is not None else None,
                "after_json": _json(after) if after is not None else None,
                "reason": reason,
            },
        )

    def create_rule_set(
        self, request: CreateAnalysisRuleSetRequest, principal: Principal
    ) -> AnalysisRuleSetRecord:
        self._require_govern(principal)
        with self._engine.begin() as connection:
            owner_count = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM iam.app_user WHERE status='ACTIVE' "
                        "AND user_id IN (:business,:technical,:quality)"
                    ),
                    {
                        "business": request.business_owner_user_id,
                        "technical": request.technical_owner_user_id,
                        "quality": request.quality_validator_user_id,
                    },
                ).scalar_one()
            )
            if owner_count != 3:
                raise DomainError(
                    "ANALYSIS_RULE_OWNER_INVALID",
                    "三类 Rule Owner 必须是不同的有效用户",
                    409,
                )
            try:
                row = (
                    connection.execute(
                        text(
                            "INSERT evaluation.rule_set(rule_code,rule_name,"
                            "evaluation_type,owner_name,business_owner_user_id,"
                            "technical_owner_user_id,quality_validator_user_id,description,active) "
                            "OUTPUT INSERTED.evaluation_rule_set_id,INSERTED.rule_code,"
                            "INSERTED.rule_name,INSERTED.evaluation_type,"
                            "INSERTED.business_owner_user_id,INSERTED.technical_owner_user_id,"
                            "INSERTED.quality_validator_user_id,INSERTED.active "
                            "VALUES(:code,:name,:rule_type,NULL,:business,:technical,"
                            ":quality,:description,1)"
                        ),
                        {
                            "code": request.rule_code,
                            "name": request.rule_name,
                            "rule_type": request.evaluation_type.value,
                            "business": request.business_owner_user_id,
                            "technical": request.technical_owner_user_id,
                            "quality": request.quality_validator_user_id,
                            "description": request.description,
                        },
                    )
                    .mappings()
                    .one()
                )
            except IntegrityError as exc:
                raise DomainError(
                    "ANALYSIS_RULE_CODE_CONFLICT", "规则代码已存在或无效", 409
                ) from exc
            record = self._rule_set(row)
            self._audit(
                connection,
                principal,
                "ANALYSIS_RULE_SET_CREATE",
                "evaluation.rule_set",
                record.evaluation_rule_set_id,
                before=None,
                after={
                    "rule_code": record.rule_code,
                    "evaluation_type": record.evaluation_type,
                    "owners": [
                        record.business_owner_user_id,
                        record.technical_owner_user_id,
                        record.quality_validator_user_id,
                    ],
                },
                reason="Create versioned analytics rule set",
            )
        return record

    def create_version(
        self,
        rule_code: str,
        request: CreateAnalysisRuleVersionRequest,
        principal: Principal,
    ) -> AnalysisRuleVersionRecord:
        self._require_govern(principal)
        with self._engine.begin() as connection:
            rule_set = self._rule_set_by_code(connection, rule_code, lock=True)
            if rule_set.evaluation_type != request.expected_rule_type.value:
                raise DomainError(
                    "ANALYSIS_RULE_ALGORITHM_TYPE_MISMATCH",
                    "算法与规则类型不一致",
                    409,
                )
            if request.supersedes_rule_version_id is not None:
                superseded = connection.execute(
                    text(
                        "SELECT evaluation_rule_set_id FROM evaluation.rule_version "
                        "WHERE evaluation_rule_version_id=:version"
                    ),
                    {"version": request.supersedes_rule_version_id},
                ).scalar_one_or_none()
                if superseded != rule_set.evaluation_rule_set_id:
                    raise DomainError(
                        "ANALYSIS_RULE_SUPERSEDES_INVALID",
                        "替代版本不属于同一规则",
                        409,
                    )
            parameters_json = _json(
                {
                    "algorithm_code": request.algorithm_code.value,
                    "parameters": request.parameters.model_dump(mode="json"),
                }
            )
            applicability_json = _json(request.applicability.model_dump(mode="json"))
            try:
                row = (
                    connection.execute(
                        text(
                            "INSERT evaluation.rule_version(evaluation_rule_set_id,"
                            "version_code,implementation_version,parameters_json,status,"
                            "approved_by,approved_at_utc,applicability_json,algorithm_sha256,"
                            "golden_manifest_sha256,effective_from_utc,effective_to_utc,"
                            "supersedes_rule_version_id,activation_status) OUTPUT "
                            "INSERTED.evaluation_rule_version_id,"
                            "INSERTED.evaluation_rule_set_id,INSERTED.version_code,"
                            "INSERTED.implementation_version,INSERTED.status,"
                            "INSERTED.activation_status VALUES(:rule_set,:version,"
                            ":implementation,:parameters,'DRAFT',NULL,NULL,:applicability,"
                            ":algorithm_sha,:golden_sha,:effective_from,:effective_to,"
                            ":supersedes,'DISABLED')"
                        ),
                        {
                            "rule_set": rule_set.evaluation_rule_set_id,
                            "version": request.version_code,
                            "implementation": request.implementation_version,
                            "parameters": parameters_json,
                            "applicability": applicability_json,
                            "algorithm_sha": request.algorithm_sha256,
                            "golden_sha": request.golden_manifest_sha256,
                            "effective_from": request.effective_from_utc,
                            "effective_to": request.effective_to_utc,
                            "supersedes": request.supersedes_rule_version_id,
                        },
                    )
                    .mappings()
                    .one()
                )
            except IntegrityError as exc:
                raise DomainError(
                    "ANALYSIS_RULE_VERSION_CONFLICT",
                    "规则版本已存在或合同无效",
                    409,
                ) from exc
            record = self._version_record(
                row,
                rule_code=rule_set.rule_code,
                algorithm_code=request.algorithm_code.value,
                approvals=(),
            )
            self._audit(
                connection,
                principal,
                "ANALYSIS_RULE_VERSION_CREATE",
                "evaluation.rule_version",
                record.evaluation_rule_version_id,
                before=None,
                after={
                    "rule_code": rule_code,
                    "version_code": request.version_code,
                    "algorithm_code": request.algorithm_code.value,
                    "activation_status": "DISABLED",
                },
                reason="Create disabled analytics rule version",
            )
        return record

    def decide(
        self,
        rule_version_id: int,
        request: DecideAnalysisRuleRequest,
        principal: Principal,
    ) -> AnalysisRuleVersionRecord:
        self._require_govern(principal)
        with self._engine.begin() as connection:
            row = self._version_row(connection, rule_version_id, lock=True)
            expected_owner = {
                RuleApprovalRole.BUSINESS: int(row["business_owner_user_id"]),
                RuleApprovalRole.TECHNICAL: int(row["technical_owner_user_id"]),
                RuleApprovalRole.QUALITY: int(row["quality_validator_user_id"]),
            }[request.approval_role]
            if principal.user_id != expected_owner:
                raise DomainError(
                    "ANALYSIS_RULE_APPROVER_MISMATCH",
                    "当前用户不是该审批角色的指定 Owner",
                    403,
                )
            if (
                request.approval_role == RuleApprovalRole.QUALITY
                and request.decision == RuleApprovalDecision.APPROVED
                and request.golden_manifest_sha256 != row["golden_manifest_sha256"]
            ):
                raise DomainError(
                    "ANALYSIS_RULE_GOLDEN_MISMATCH",
                    "质量审批的 Golden SHA 与规则版本不一致",
                    409,
                )
            connection.execute(
                text(
                    "INSERT evaluation.rule_approval_record("
                    "evaluation_rule_version_id,approval_role,approver_user_id,"
                    "decision,decision_note,golden_manifest_sha256) VALUES("
                    ":version,:role,:user,:decision,:note,:golden)"
                ),
                {
                    "version": rule_version_id,
                    "role": request.approval_role.value,
                    "user": principal.user_id,
                    "decision": request.decision.value,
                    "note": request.decision_note,
                    "golden": request.golden_manifest_sha256,
                },
            )
            approvals = self._latest_approvals(connection, rule_version_id)
            fully_approved = approvals == {
                "BUSINESS": "APPROVED",
                "TECHNICAL": "APPROVED",
                "QUALITY": "APPROVED",
            }
            new_status = "RELEASED" if fully_approved else "DRAFT"
            connection.execute(
                text(
                    "UPDATE evaluation.rule_version SET status=:status,"
                    "approved_by=CASE WHEN :approved=1 THEN :business ELSE NULL END,"
                    "approved_at_utc=CASE WHEN :approved=1 THEN SYSUTCDATETIME() ELSE NULL END,"
                    "activation_status=CASE WHEN :approved=1 THEN activation_status "
                    "ELSE 'DISABLED' END WHERE evaluation_rule_version_id=:version"
                ),
                {
                    "status": new_status,
                    "approved": int(fully_approved),
                    "business": int(row["business_owner_user_id"]),
                    "version": rule_version_id,
                },
            )
            if not fully_approved:
                connection.execute(
                    text(
                        "UPDATE evaluation.rule_activation SET active=0 "
                        "WHERE evaluation_rule_version_id=:version AND active=1"
                    ),
                    {"version": rule_version_id},
                )
            self._audit(
                connection,
                principal,
                "ANALYSIS_RULE_APPROVAL_DECIDE",
                "evaluation.rule_version",
                rule_version_id,
                before={"status": row["status"]},
                after={
                    "role": request.approval_role.value,
                    "decision": request.decision.value,
                    "status": new_status,
                },
                reason=request.decision_note,
            )
        return self.get_version(rule_version_id, principal)

    def activate(
        self,
        rule_version_id: int,
        request: ActivateAnalysisRuleRequest,
        principal: Principal,
    ) -> AnalysisRuleActivationRecord:
        self._require_govern(principal)
        with self._engine.begin() as connection:
            row = self._version_row(connection, rule_version_id, lock=True)
            approvals = self._latest_approvals(connection, rule_version_id)
            if str(row["status"]) != "RELEASED" or approvals != {
                "BUSINESS": "APPROVED",
                "TECHNICAL": "APPROVED",
                "QUALITY": "APPROVED",
            }:
                raise DomainError(
                    "ANALYSIS_RULE_NOT_APPROVED",
                    "规则未完成 Business、Technical、Quality 三方批准",
                    409,
                )
            applicability = json.loads(str(row["applicability_json"]))
            if request.test_stage not in applicability.get("test_stages", []):
                raise DomainError(
                    "ANALYSIS_RULE_SCOPE_INVALID", "激活 Stage 不在批准适用范围", 409
                )
            approved_suppliers = set(applicability.get("supplier_ids", []))
            approved_products = set(applicability.get("product_ids", []))
            approved_patterns = tuple(applicability.get("parameter_patterns", []))
            if approved_suppliers and request.supplier_id not in approved_suppliers:
                raise DomainError(
                    "ANALYSIS_RULE_SCOPE_INVALID",
                    "激活 Supplier 不在批准适用范围",
                    409,
                )
            if approved_products and request.product_id not in approved_products:
                raise DomainError(
                    "ANALYSIS_RULE_SCOPE_INVALID",
                    "激活 Product 不在批准适用范围",
                    409,
                )
            if approved_patterns and (
                request.parameter_pattern is None
                or not any(
                    _pattern_matches(pattern, request.parameter_pattern.rstrip("*"))
                    and (
                        not request.parameter_pattern.endswith("*")
                        or pattern.endswith("*")
                        and request.parameter_pattern[:-1].startswith(pattern[:-1])
                    )
                    for pattern in approved_patterns
                )
            ):
                raise DomainError(
                    "ANALYSIS_RULE_SCOPE_INVALID",
                    "激活 Parameter 范围超出批准适用范围",
                    409,
                )
            active_scopes = (
                connection.execute(
                    text(
                        "SELECT ra.supplier_id,ra.product_id,ra.parameter_pattern "
                        "FROM evaluation.rule_activation ra "
                        "JOIN evaluation.rule_version existing_version ON "
                        "existing_version.evaluation_rule_version_id="
                        "ra.evaluation_rule_version_id "
                        "WHERE existing_version.evaluation_rule_set_id="
                        ":rule_set AND ra.active=1 AND ra.test_stage=:stage"
                    ),
                    {
                        "rule_set": int(row["evaluation_rule_set_id"]),
                        "stage": request.test_stage,
                    },
                )
                .mappings()
                .all()
            )
            overlapping = any(
                (
                    scope["supplier_id"] is None
                    or request.supplier_id is None
                    or int(scope["supplier_id"]) == request.supplier_id
                )
                and (
                    scope["product_id"] is None
                    or request.product_id is None
                    or int(scope["product_id"]) == request.product_id
                )
                and _pattern_overlaps(
                    str(scope["parameter_pattern"])
                    if scope["parameter_pattern"] is not None
                    else None,
                    request.parameter_pattern,
                )
                for scope in active_scopes
            )
            if overlapping:
                raise DomainError(
                    "ANALYSIS_RULE_ACTIVATION_CONFLICT",
                    "同一规则已有重叠的激活范围",
                    409,
                )
            activation = (
                connection.execute(
                    text(
                        "INSERT evaluation.rule_activation("
                        "evaluation_rule_version_id,test_stage,supplier_id,product_id,"
                        "parameter_pattern,active,activated_by_user_id,effective_from_utc,"
                        "effective_to_utc) OUTPUT INSERTED.rule_activation_id,"
                        "INSERTED.evaluation_rule_version_id,INSERTED.test_stage,"
                        "INSERTED.supplier_id,INSERTED.product_id,INSERTED.parameter_pattern,"
                        "INSERTED.active VALUES(:version,:stage,:supplier,:product,"
                        ":parameter,1,:user,:effective_from,:effective_to)"
                    ),
                    {
                        "version": rule_version_id,
                        "stage": request.test_stage,
                        "supplier": request.supplier_id,
                        "product": request.product_id,
                        "parameter": request.parameter_pattern,
                        "user": principal.user_id,
                        "effective_from": request.effective_from_utc,
                        "effective_to": request.effective_to_utc,
                    },
                )
                .mappings()
                .one()
            )
            connection.execute(
                text(
                    "UPDATE evaluation.rule_version SET activation_status='ENABLED' "
                    "WHERE evaluation_rule_version_id=:version"
                ),
                {"version": rule_version_id},
            )
            record = self._activation(activation)
            self._audit(
                connection,
                principal,
                "ANALYSIS_RULE_ACTIVATE",
                "evaluation.rule_activation",
                record.rule_activation_id,
                before=None,
                after={
                    "rule_version_id": rule_version_id,
                    "stage": request.test_stage,
                    "supplier_id": request.supplier_id,
                    "product_id": request.product_id,
                    "parameter_pattern": request.parameter_pattern,
                },
                reason="Activate fully approved analytics rule",
            )
        return record

    def get_version(
        self, rule_version_id: int, principal: Principal
    ) -> AnalysisRuleVersionRecord:
        self._require_govern(principal)
        with self._engine.connect() as connection:
            row = self._version_row(connection, rule_version_id, lock=False)
            approvals = self._latest_approvals(connection, rule_version_id)
        parameters = json.loads(str(row["parameters_json"]))
        return self._version_record(
            row,
            rule_code=str(row["rule_code"]),
            algorithm_code=str(parameters["algorithm_code"]),
            approvals=tuple(
                f"{role}:{decision}" for role, decision in sorted(approvals.items())
            ),
        )

    def list_versions(
        self, rule_code: str, principal: Principal
    ) -> tuple[AnalysisRuleVersionRecord, ...]:
        self._require_govern(principal)
        with self._engine.connect() as connection:
            rule_set = self._rule_set_by_code(connection, rule_code, lock=False)
            rows = (
                connection.execute(
                    text(
                        "SELECT rv.evaluation_rule_version_id,"
                        "rv.evaluation_rule_set_id,rv.version_code,"
                        "rv.implementation_version,rv.parameters_json,rv.status,"
                        "rv.activation_status FROM evaluation.rule_version rv "
                        "WHERE rv.evaluation_rule_set_id=:rule_set "
                        "ORDER BY rv.created_at_utc DESC,"
                        "rv.evaluation_rule_version_id DESC"
                    ),
                    {"rule_set": rule_set.evaluation_rule_set_id},
                )
                .mappings()
                .all()
            )
            records: list[AnalysisRuleVersionRecord] = []
            for row in rows:
                try:
                    contract = json.loads(str(row["parameters_json"]))
                    algorithm_code = str(contract["algorithm_code"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise DomainError(
                        "ANALYSIS_RULE_CONTRACT_INVALID",
                        "规则版本参数合同无效",
                        409,
                    ) from exc
                approvals = self._latest_approvals(
                    connection, int(row["evaluation_rule_version_id"])
                )
                records.append(
                    self._version_record(
                        row,
                        rule_code=rule_set.rule_code,
                        algorithm_code=algorithm_code,
                        approvals=tuple(
                            f"{role}:{decision}"
                            for role, decision in sorted(approvals.items())
                        ),
                    )
                )
        return tuple(records)

    def list_rule_sets(self, principal: Principal) -> tuple[AnalysisRuleSetRecord, ...]:
        self._require_govern(principal)
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT evaluation_rule_set_id,rule_code,rule_name,evaluation_type,"
                        "business_owner_user_id,technical_owner_user_id,"
                        "quality_validator_user_id,active FROM evaluation.rule_set "
                        "WHERE business_owner_user_id IS NOT NULL "
                        "ORDER BY rule_code"
                    )
                )
                .mappings()
                .all()
            )
        return tuple(self._rule_set(row) for row in rows)

    def assert_rule_approved(
        self,
        *,
        rule_code: str,
        version_code: str,
        test_stage: str,
        supplier_id: int | None = None,
        product_id: int | None = None,
        parameter: str | None = None,
    ) -> None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT TOP (1) rv.evaluation_rule_version_id,rv.status,"
                        "rv.activation_status FROM evaluation.rule_set rs "
                        "JOIN evaluation.rule_version rv "
                        "ON rv.evaluation_rule_set_id=rs.evaluation_rule_set_id "
                        "JOIN evaluation.rule_activation ra "
                        "ON ra.evaluation_rule_version_id=rv.evaluation_rule_version_id "
                        "WHERE rs.rule_code=:code AND rv.version_code=:version "
                        "AND rs.active=1 AND rv.status='RELEASED' "
                        "AND rv.activation_status='ENABLED' AND ra.active=1 "
                        "AND ra.test_stage=:stage "
                        "AND (ra.supplier_id IS NULL OR ra.supplier_id=:supplier) "
                        "AND (ra.product_id IS NULL OR ra.product_id=:product) "
                        "AND (ra.parameter_pattern IS NULL "
                        "OR ra.parameter_pattern=:parameter "
                        "OR (RIGHT(ra.parameter_pattern,1)='*' AND :parameter IS NOT NULL "
                        "AND LEFT(:parameter,LEN(ra.parameter_pattern)-1)="
                        "LEFT(ra.parameter_pattern,LEN(ra.parameter_pattern)-1))) "
                        "AND (ra.effective_from_utc IS NULL OR ra.effective_from_utc<=SYSUTCDATETIME()) "
                        "AND (ra.effective_to_utc IS NULL OR ra.effective_to_utc>SYSUTCDATETIME())"
                    ),
                    {
                        "code": rule_code,
                        "version": version_code,
                        "stage": test_stage,
                        "supplier": supplier_id,
                        "product": product_id,
                        "parameter": parameter,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise DomainError(
                    "ANALYSIS_RULE_NOT_APPROVED",
                    "请求的规则版本未获批准或未在当前范围激活",
                    409,
                )
            approvals = self._latest_approvals(
                connection, int(row["evaluation_rule_version_id"])
            )
            if approvals != {
                "BUSINESS": "APPROVED",
                "TECHNICAL": "APPROVED",
                "QUALITY": "APPROVED",
            }:
                raise DomainError(
                    "ANALYSIS_RULE_NOT_APPROVED",
                    "请求的规则版本审批已撤销或不完整",
                    409,
                )

    def approved_rule_parameters(
        self,
        *,
        rule_code: str,
        version_code: str,
        test_stage: str,
        expected_algorithm_code: str,
        supplier_id: int | None = None,
        product_id: int | None = None,
        parameter: str | None = None,
    ) -> dict[str, Any]:
        """Resolve configuration only after the approval and activation gate passes."""
        self.assert_rule_approved(
            rule_code=rule_code,
            version_code=version_code,
            test_stage=test_stage,
            supplier_id=supplier_id,
            product_id=product_id,
            parameter=parameter,
        )
        with self._engine.connect() as connection:
            raw = connection.execute(
                text(
                    "SELECT rv.parameters_json FROM evaluation.rule_set rs "
                    "JOIN evaluation.rule_version rv "
                    "ON rv.evaluation_rule_set_id=rs.evaluation_rule_set_id "
                    "WHERE rs.rule_code=:code AND rv.version_code=:version "
                    "AND rs.active=1 AND rv.status='RELEASED' "
                    "AND rv.activation_status='ENABLED'"
                ),
                {"code": rule_code, "version": version_code},
            ).scalar_one_or_none()
        if raw is None:
            raise DomainError(
                "ANALYSIS_RULE_NOT_APPROVED",
                "请求的规则版本未获批准或未激活",
                409,
            )
        try:
            contract = json.loads(str(raw))
            algorithm_code = contract["algorithm_code"]
            parameters = contract["parameters"]
        except (KeyError, TypeError, ValueError) as exc:
            raise DomainError(
                "ANALYSIS_RULE_CONTRACT_INVALID",
                "规则版本参数合同无效",
                409,
            ) from exc
        if algorithm_code != expected_algorithm_code or not isinstance(
            parameters, dict
        ):
            raise DomainError(
                "ANALYSIS_RULE_ALGORITHM_TYPE_MISMATCH",
                "激活规则算法与请求的分析能力不一致",
                409,
            )
        return dict(parameters)

    @staticmethod
    def _latest_approvals(
        connection: Connection, rule_version_id: int
    ) -> dict[str, str]:
        rows = (
            connection.execute(
                text(
                    ";WITH ranked AS(SELECT approval_role,decision,"
                    "ROW_NUMBER() OVER(PARTITION BY approval_role "
                    "ORDER BY decided_at_utc DESC,rule_approval_id DESC) AS rn "
                    "FROM evaluation.rule_approval_record "
                    "WHERE evaluation_rule_version_id=:version) "
                    "SELECT approval_role,decision FROM ranked WHERE rn=1"
                ),
                {"version": rule_version_id},
            )
            .mappings()
            .all()
        )
        return {str(row["approval_role"]): str(row["decision"]) for row in rows}

    @staticmethod
    def _rule_set_by_code(
        connection: Connection, rule_code: str, *, lock: bool
    ) -> AnalysisRuleSetRecord:
        hint = " WITH (UPDLOCK,HOLDLOCK)" if lock else ""
        row = (
            connection.execute(
                text(
                    "SELECT evaluation_rule_set_id,rule_code,rule_name,evaluation_type,"
                    "business_owner_user_id,technical_owner_user_id,"
                    "quality_validator_user_id,active FROM evaluation.rule_set"
                    + hint
                    + " WHERE rule_code=:code AND active=1"
                ),
                {"code": rule_code},
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["business_owner_user_id"] is None:
            raise DomainError("ANALYSIS_RULE_NOT_FOUND", "规则不存在", 404)
        return SqlAnalysisRuleService._rule_set(row)

    @staticmethod
    def _version_row(
        connection: Connection, rule_version_id: int, *, lock: bool
    ) -> Mapping[str, Any]:
        hint = " WITH (UPDLOCK,HOLDLOCK)" if lock else ""
        row = (
            connection.execute(
                text(
                    "SELECT rv.evaluation_rule_version_id,rv.evaluation_rule_set_id,"
                    "rv.version_code,rv.implementation_version,rv.parameters_json,"
                    "rv.status,rv.activation_status,rv.golden_manifest_sha256,"
                    "rv.applicability_json,rs.rule_code,rs.business_owner_user_id,"
                    "rs.technical_owner_user_id,rs.quality_validator_user_id "
                    "FROM evaluation.rule_version rv"
                    + hint
                    + " JOIN evaluation.rule_set rs "
                    "ON rs.evaluation_rule_set_id=rv.evaluation_rule_set_id "
                    "WHERE rv.evaluation_rule_version_id=:version AND rs.active=1"
                ),
                {"version": rule_version_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DomainError("ANALYSIS_RULE_VERSION_NOT_FOUND", "规则版本不存在", 404)
        return row

    @staticmethod
    def _rule_set(row: Mapping[str, Any]) -> AnalysisRuleSetRecord:
        return AnalysisRuleSetRecord(
            evaluation_rule_set_id=int(row["evaluation_rule_set_id"]),
            rule_code=str(row["rule_code"]),
            rule_name=str(row["rule_name"]),
            evaluation_type=str(row["evaluation_type"]),
            business_owner_user_id=int(row["business_owner_user_id"]),
            technical_owner_user_id=int(row["technical_owner_user_id"]),
            quality_validator_user_id=int(row["quality_validator_user_id"]),
            active=bool(row["active"]),
        )

    @staticmethod
    def _version_record(
        row: Mapping[str, Any],
        *,
        rule_code: str,
        algorithm_code: str,
        approvals: tuple[str, ...],
    ) -> AnalysisRuleVersionRecord:
        return AnalysisRuleVersionRecord(
            evaluation_rule_version_id=int(row["evaluation_rule_version_id"]),
            evaluation_rule_set_id=int(row["evaluation_rule_set_id"]),
            rule_code=rule_code,
            version_code=str(row["version_code"]),
            implementation_version=str(row["implementation_version"]),
            status=str(row["status"]),
            activation_status=str(row["activation_status"]),
            algorithm_code=algorithm_code,
            approvals=approvals,
        )

    @staticmethod
    def _activation(row: Mapping[str, Any]) -> AnalysisRuleActivationRecord:
        return AnalysisRuleActivationRecord(
            rule_activation_id=int(row["rule_activation_id"]),
            evaluation_rule_version_id=int(row["evaluation_rule_version_id"]),
            test_stage=str(row["test_stage"]),
            supplier_id=(
                int(row["supplier_id"]) if row["supplier_id"] is not None else None
            ),
            product_id=(
                int(row["product_id"]) if row["product_id"] is not None else None
            ),
            parameter_pattern=(
                str(row["parameter_pattern"])
                if row["parameter_pattern"] is not None
                else None
            ),
            active=bool(row["active"]),
        )
