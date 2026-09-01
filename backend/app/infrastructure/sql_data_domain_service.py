from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.data_domains import (
    CreateDataDomainGrantRequest,
    CreateDataDomainRequest,
    DataDomainGrantRecord,
    DataDomainRecord,
    GrantableUserRecord,
    UpdateDataDomainRequest,
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).replace(tzinfo=None)


def _grant_record(row: Mapping[str, Any]) -> DataDomainGrantRecord:
    return DataDomainGrantRecord(
        user_id=int(row["user_id"]),
        login_name=str(row["login_name"]),
        display_name=str(row["display_name"]),
        expires_at_utc=_iso(row["expires_at_utc"]),
        granted_at_utc=_iso(row["granted_at_utc"]) or "",
        reason=str(row["reason"]) if row["reason"] is not None else None,
    )


def _domain_record(
    row: Mapping[str, Any],
    *,
    grant_expires_at_utc: datetime | None = None,
    grants: tuple[DataDomainGrantRecord, ...] = (),
) -> DataDomainRecord:
    return DataDomainRecord(
        data_domain_id=int(row["data_domain_id"]),
        domain_code=str(row["domain_code"]),
        domain_name=str(row["domain_name"]),
        test_stage=str(row["test_stage"]),
        factory_code=str(row["factory_code"])
        if row["factory_code"] is not None
        else None,
        active=bool(row["active"]),
        grant_expires_at_utc=_iso(grant_expires_at_utc),
        grants=grants,
    )


class SqlDataDomainService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_for_principal(
        self, principal: Principal
    ) -> tuple[DataDomainRecord, ...]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT d.data_domain_id,d.domain_code,d.domain_name,d.test_stage,"
                        "d.factory_code,d.active,g.expires_at_utc "
                        "FROM iam.data_domain_grant g "
                        "JOIN iam.data_domain d ON d.data_domain_id=g.data_domain_id "
                        "WHERE g.user_id=:user_id AND g.status='ACTIVE' AND d.active=1 "
                        "AND (g.expires_at_utc IS NULL OR "
                        "g.expires_at_utc>SYSUTCDATETIME()) "
                        "ORDER BY d.test_stage,d.domain_name,d.data_domain_id"
                    ),
                    {"user_id": principal.user_id},
                )
                .mappings()
                .all()
            )
        return tuple(
            _domain_record(row, grant_expires_at_utc=row["expires_at_utc"])
            for row in rows
        )

    def list_admin(self) -> tuple[DataDomainRecord, ...]:
        with self._engine.connect() as connection:
            domains = (
                connection.execute(
                    text(
                        "SELECT data_domain_id,domain_code,domain_name,test_stage,"
                        "factory_code,active FROM iam.data_domain "
                        "WHERE domain_code<>N'MIGRATION_HOLD' "
                        "ORDER BY test_stage,domain_name,data_domain_id"
                    )
                )
                .mappings()
                .all()
            )
            grant_rows = (
                connection.execute(
                    text(
                        "SELECT g.data_domain_id,g.user_id,u.login_name,u.display_name,"
                        "g.expires_at_utc,g.granted_at_utc,g.reason "
                        "FROM iam.data_domain_grant g "
                        "JOIN iam.app_user u ON u.user_id=g.user_id "
                        "WHERE g.status='ACTIVE' AND "
                        "(g.expires_at_utc IS NULL OR "
                        "g.expires_at_utc>SYSUTCDATETIME()) "
                        "ORDER BY g.data_domain_id,u.login_name,u.user_id"
                    )
                )
                .mappings()
                .all()
            )
        grants_by_domain: dict[int, list[DataDomainGrantRecord]] = {}
        for row in grant_rows:
            grants_by_domain.setdefault(int(row["data_domain_id"]), []).append(
                _grant_record(row)
            )
        return tuple(
            _domain_record(
                row,
                grants=tuple(grants_by_domain.get(int(row["data_domain_id"]), ())),
            )
            for row in domains
        )

    def list_grantable_users(self) -> tuple[GrantableUserRecord, ...]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT user_id,login_name,display_name FROM iam.app_user "
                        "WHERE status='ACTIVE' ORDER BY display_name,login_name,user_id"
                    )
                )
                .mappings()
                .all()
            )
        return tuple(
            GrantableUserRecord(
                user_id=int(row["user_id"]),
                login_name=str(row["login_name"]),
                display_name=str(row["display_name"]),
            )
            for row in rows
        )

    def create(
        self, request: CreateDataDomainRequest, principal: Principal
    ) -> DataDomainRecord:
        try:
            with self._engine.begin() as connection:
                row = (
                    connection.execute(
                        text(
                            "INSERT iam.data_domain(domain_code,domain_name,test_stage,"
                            "factory_code,active,created_by_user_id) OUTPUT "
                            "INSERTED.data_domain_id,INSERTED.domain_code,"
                            "INSERTED.domain_name,INSERTED.test_stage,"
                            "INSERTED.factory_code,INSERTED.active VALUES("
                            ":domain_code,:domain_name,:test_stage,:factory_code,"
                            ":active,:created_by_user_id)"
                        ),
                        request.model_dump(mode="python")
                        | {"created_by_user_id": principal.user_id},
                    )
                    .mappings()
                    .one()
                )
                self._audit(
                    connection,
                    principal,
                    operation="DATA_DOMAIN_CREATED",
                    entity_id=int(row["data_domain_id"]),
                    reason="create data domain",
                    after=request.model_dump(mode="json"),
                )
            return _domain_record(row)
        except IntegrityError as exc:
            raise DomainError(
                "DATA_DOMAIN_CONFLICT", "数据域编码已存在或关联用户无效", 409
            ) from exc

    def update(
        self,
        data_domain_id: int,
        request: UpdateDataDomainRequest,
        principal: Principal,
    ) -> DataDomainRecord:
        with self._engine.begin() as connection:
            before = connection.execute(
                text(
                    "SELECT data_domain_id,domain_code,domain_name,test_stage,"
                    "factory_code,active FROM iam.data_domain WITH (UPDLOCK,HOLDLOCK) "
                    "WHERE data_domain_id=:domain AND domain_code<>N'MIGRATION_HOLD'"
                ),
                {"domain": data_domain_id},
            ).mappings().one_or_none()
            if before is None:
                raise DomainError("DATA_DOMAIN_NOT_FOUND", "数据域不存在", 404)
            row = (
                connection.execute(
                    text(
                        "UPDATE iam.data_domain SET domain_name=:domain_name,"
                        "factory_code=:factory_code,active=:active,"
                        "updated_at_utc=SYSUTCDATETIME() OUTPUT "
                        "INSERTED.data_domain_id,INSERTED.domain_code,"
                        "INSERTED.domain_name,INSERTED.test_stage,"
                        "INSERTED.factory_code,INSERTED.active "
                        "WHERE data_domain_id=:domain"
                    ),
                    request.model_dump(mode="python") | {"domain": data_domain_id},
                )
                .mappings()
                .one()
            )
            self._audit(
                connection,
                principal,
                operation="DATA_DOMAIN_UPDATED",
                entity_id=data_domain_id,
                reason="update data domain",
                before=dict(before),
                after=request.model_dump(mode="json"),
            )
        return _domain_record(row)

    def grant(
        self,
        data_domain_id: int,
        request: CreateDataDomainGrantRequest,
        principal: Principal,
    ) -> DataDomainGrantRecord:
        expires = _utc_naive(request.expires_at_utc)
        with self._engine.begin() as connection:
            domain = connection.execute(
                text(
                    "SELECT domain_code FROM iam.data_domain WITH (UPDLOCK,HOLDLOCK) "
                    "WHERE data_domain_id=:domain AND active=1 "
                    "AND domain_code<>N'MIGRATION_HOLD'"
                ),
                {"domain": data_domain_id},
            ).scalar_one_or_none()
            if domain is None:
                raise DomainError(
                    "DATA_DOMAIN_NOT_FOUND", "数据域不存在或已停用", 404
                )
            user = connection.execute(
                text(
                    "SELECT user_id,login_name,display_name FROM iam.app_user "
                    "WHERE user_id=:user AND status='ACTIVE'"
                ),
                {"user": request.user_id},
            ).mappings().one_or_none()
            if user is None:
                raise DomainError(
                    "GRANTEE_NOT_ACTIVE", "被授权用户不存在或未启用", 422
                )
            existing = connection.execute(
                text(
                    "SELECT data_domain_grant_id FROM iam.data_domain_grant "
                    "WITH (UPDLOCK,HOLDLOCK) WHERE data_domain_id=:domain "
                    "AND user_id=:user AND status='ACTIVE'"
                ),
                {"domain": data_domain_id, "user": request.user_id},
            ).scalar_one_or_none()
            if existing is None:
                grant_row = (
                    connection.execute(
                        text(
                            "INSERT iam.data_domain_grant(data_domain_id,user_id,status,"
                            "granted_by_user_id,expires_at_utc,reason) OUTPUT "
                            "INSERTED.user_id,INSERTED.expires_at_utc,"
                            "INSERTED.granted_at_utc,INSERTED.reason VALUES("
                            ":domain,:user,'ACTIVE',:actor,:expires,:reason)"
                        ),
                        {
                            "domain": data_domain_id,
                            "user": request.user_id,
                            "actor": principal.user_id,
                            "expires": expires,
                            "reason": request.reason,
                        },
                    )
                    .mappings()
                    .one()
                )
            else:
                grant_row = (
                    connection.execute(
                        text(
                            "UPDATE iam.data_domain_grant SET expires_at_utc=:expires,"
                            "reason=:reason,granted_by_user_id=:actor,"
                            "granted_at_utc=SYSUTCDATETIME() OUTPUT "
                            "INSERTED.user_id,INSERTED.expires_at_utc,"
                            "INSERTED.granted_at_utc,INSERTED.reason "
                            "WHERE data_domain_grant_id=:grant"
                        ),
                        {
                            "grant": int(existing),
                            "actor": principal.user_id,
                            "expires": expires,
                            "reason": request.reason,
                        },
                    )
                    .mappings()
                    .one()
                )
            self._audit(
                connection,
                principal,
                operation="DATA_DOMAIN_GRANTED",
                entity_id=data_domain_id,
                reason=request.reason,
                after={
                    "user_id": request.user_id,
                    "expires_at_utc": _iso(request.expires_at_utc),
                },
            )
        return _grant_record(dict(grant_row) | dict(user))

    def revoke(
        self, data_domain_id: int, user_id: int, principal: Principal
    ) -> None:
        with self._engine.begin() as connection:
            updated = connection.execute(
                text(
                    "UPDATE iam.data_domain_grant SET status='REVOKED',"
                    "revoked_by_user_id=:actor,revoked_at_utc=SYSUTCDATETIME() "
                    "WHERE data_domain_id=:domain AND user_id=:user "
                    "AND status='ACTIVE'"
                ),
                {
                    "domain": data_domain_id,
                    "user": user_id,
                    "actor": principal.user_id,
                },
            )
            if updated.rowcount != 1:
                raise DomainError(
                    "DATA_DOMAIN_GRANT_NOT_FOUND", "当前有效数据域授权不存在", 404
                )
            self._audit(
                connection,
                principal,
                operation="DATA_DOMAIN_REVOKED",
                entity_id=data_domain_id,
                reason="revoke data-domain grant",
                before={"user_id": user_id, "status": "ACTIVE"},
                after={"user_id": user_id, "status": "REVOKED"},
            )

    @staticmethod
    def _audit(
        connection: Any,
        principal: Principal,
        *,
        operation: str,
        entity_id: int,
        reason: str,
        before: Mapping[str, Any] | None = None,
        after: Mapping[str, Any] | None = None,
    ) -> None:
        connection.execute(
            text(
                "INSERT governance.audit_log(actor,operation,entity_type,entity_id,"
                "before_json,after_json,reason,actor_user_id) VALUES("
                ":actor,:operation,'iam.data_domain',:entity,:before_json,"
                ":after_json,:reason,:actor_user_id)"
            ),
            {
                "actor": principal.login_name[:128],
                "operation": operation,
                "entity": str(entity_id),
                "before_json": json.dumps(before, ensure_ascii=False, default=str)
                if before is not None
                else None,
                "after_json": json.dumps(after, ensure_ascii=False, default=str)
                if after is not None
                else None,
                "reason": reason,
                "actor_user_id": principal.user_id,
            },
        )
