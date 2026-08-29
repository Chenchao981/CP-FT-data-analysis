from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine, text

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.master_data import (
    ApproveProductCrosswalkRequest,
    ProductCrosswalk,
    ProductCrosswalkPage,
    RejectProductCrosswalkRequest,
)

_CROSSWALK_COLUMNS = """
cw.crosswalk_id,cw.supplier_id,s.supplier_code,s.supplier_name,cw.test_stage,
cw.raw_product_code,cw.product_id,p.product_code AS tms_product_code,
p.identity_class,cw.enterprise_system,cw.enterprise_key,cw.status,
cw.first_observed_at_utc,cw.last_observed_at_utc,u.login_name AS approved_by_login,
cw.approved_at_utc,cw.decision_reason
"""


def observe_product_crosswalk(
    connection: Connection,
    *,
    supplier_id: int,
    product_id: int,
    test_stage: str,
    raw_product_code: str,
) -> None:
    """Record a source identity without asserting an SAP material mapping."""

    code = raw_product_code.strip()
    stage = test_stage.strip().upper()
    if not code or len(code) > 200 or stage not in {"CP", "FT"}:
        raise DomainError(
            "SOURCE_PRODUCT_IDENTITY_INVALID",
            "源产品标识无效，不能登记主数据映射",
            422,
        )
    connection.execute(
        text(
            "UPDATE mdm.enterprise_product_crosswalk WITH (UPDLOCK,HOLDLOCK) SET "
            "last_observed_at_utc=SYSUTCDATETIME(),product_id=:product "
            "WHERE source_system='TMS_SOURCE' AND supplier_id=:supplier "
            "AND test_stage=:stage AND raw_product_code=:raw_code; "
            "IF @@ROWCOUNT=0 INSERT mdm.enterprise_product_crosswalk("
            "supplier_id,test_stage,raw_product_code,product_id,status) "
            "VALUES(:supplier,:stage,:raw_code,:product,'PENDING')"
        ),
        {
            "supplier": supplier_id,
            "product": product_id,
            "stage": stage,
            "raw_code": code,
        },
    )


def _iso_utc(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise DomainError(
            "CROSSWALK_DATA_INVALID", "主数据映射包含无效时间", 503
        )
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _to_crosswalk(row: Mapping[str, Any]) -> ProductCrosswalk:
    return ProductCrosswalk(
        crosswalk_id=int(row["crosswalk_id"]),
        supplier_id=int(row["supplier_id"]),
        supplier_code=str(row["supplier_code"]),
        supplier_name=str(row["supplier_name"]),
        test_stage=str(row["test_stage"]),
        raw_product_code=str(row["raw_product_code"]),
        product_id=int(row["product_id"]),
        tms_product_code=str(row["tms_product_code"]),
        identity_class=str(row["identity_class"]),
        enterprise_system=str(row["enterprise_system"]),
        enterprise_key=(
            str(row["enterprise_key"])
            if row["enterprise_key"] is not None
            else None
        ),
        status=str(row["status"]),
        first_observed_at_utc=_iso_utc(row["first_observed_at_utc"]) or "",
        last_observed_at_utc=_iso_utc(row["last_observed_at_utc"]) or "",
        approved_by_login=(
            str(row["approved_by_login"])
            if row["approved_by_login"] is not None
            else None
        ),
        approved_at_utc=_iso_utc(row["approved_at_utc"]),
        decision_reason=(
            str(row["decision_reason"])
            if row["decision_reason"] is not None
            else None
        ),
    )


class SqlMasterDataService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_product_crosswalks(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        supplier_code: str | None = None,
        test_stage: str | None = None,
        raw_product_code: str | None = None,
    ) -> ProductCrosswalkPage:
        if page < 1 or page_size < 1 or page_size > 100:
            raise DomainError(
                "CROSSWALK_PAGE_INVALID", "主数据映射分页参数无效", 422
            )
        clauses: list[str] = []
        params: dict[str, Any] = {
            "offset": (page - 1) * page_size,
            "page_size": page_size,
        }
        for key, value, column in (
            ("status", status, "cw.status"),
            ("supplier_code", supplier_code, "s.supplier_code"),
            ("test_stage", test_stage, "cw.test_stage"),
        ):
            if value is not None:
                clauses.append(f"AND {column}=:{key}")
                params[key] = value
        if raw_product_code is not None:
            clauses.append("AND cw.raw_product_code LIKE :raw_product_code")
            params["raw_product_code"] = f"%{_escape_like(raw_product_code)}%"
        where = "\n".join(clauses)
        base = (
            " FROM mdm.enterprise_product_crosswalk cw "
            "JOIN mdm.supplier s ON s.supplier_id=cw.supplier_id "
            "JOIN mdm.product p ON p.product_id=cw.product_id "
            "LEFT JOIN iam.app_user u ON u.user_id=cw.approved_by "
            f"WHERE 1=1 {where}"
        )
        try:
            with self._engine.connect() as connection:
                total = int(
                    connection.execute(
                        text("SELECT COUNT_BIG(*)" + base), params
                    ).scalar_one()
                )
                rows = (
                    connection.execute(
                        text(
                            f"SELECT {_CROSSWALK_COLUMNS}"
                            + base
                            + " ORDER BY CASE cw.status WHEN 'PENDING' THEN 0 "
                            "WHEN 'REJECTED' THEN 1 ELSE 2 END,"
                            "cw.last_observed_at_utc DESC,cw.crosswalk_id DESC "
                            "OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY"
                        ),
                        params,
                    )
                    .mappings()
                    .all()
                )
        except Exception as exc:
            raise DomainError(
                "CROSSWALK_LIST_UNAVAILABLE",
                "主数据映射列表暂时不可用",
                503,
            ) from exc
        return ProductCrosswalkPage(
            items=tuple(_to_crosswalk(row) for row in rows),
            total=total,
            page=page,
            page_size=page_size,
        )

    def approve_product_crosswalk(
        self,
        crosswalk_id: int,
        request: ApproveProductCrosswalkRequest,
        principal: Principal,
    ) -> ProductCrosswalk:
        return self._decide(
            crosswalk_id,
            principal,
            status="APPROVED",
            reason=request.reason,
            enterprise_key=request.enterprise_key,
        )

    def reject_product_crosswalk(
        self,
        crosswalk_id: int,
        request: RejectProductCrosswalkRequest,
        principal: Principal,
    ) -> ProductCrosswalk:
        return self._decide(
            crosswalk_id,
            principal,
            status="REJECTED",
            reason=request.reason,
            enterprise_key=None,
        )

    def _decide(
        self,
        crosswalk_id: int,
        principal: Principal,
        *,
        status: str,
        reason: str,
        enterprise_key: str | None,
    ) -> ProductCrosswalk:
        if crosswalk_id < 1:
            raise DomainError(
                "CROSSWALK_ID_INVALID", "主数据映射标识无效", 422
            )
        try:
            with self._engine.begin() as connection:
                before = (
                    connection.execute(
                        text(
                            "SELECT status,enterprise_system,enterprise_key,product_id "
                            "FROM mdm.enterprise_product_crosswalk WITH (UPDLOCK,HOLDLOCK) "
                            "WHERE crosswalk_id=:crosswalk"
                        ),
                        {"crosswalk": crosswalk_id},
                    )
                    .mappings()
                    .one_or_none()
                )
                if before is None:
                    raise DomainError(
                        "CROSSWALK_NOT_FOUND", "主数据映射不存在", 404
                    )
                if str(before["status"]) == "RETIRED":
                    raise DomainError(
                        "CROSSWALK_RETIRED", "已停用映射不能直接重新审批", 409
                    )
                updated = connection.execute(
                    text(
                        "UPDATE mdm.enterprise_product_crosswalk SET status=:status,"
                        "enterprise_system='SAP_B1',enterprise_key=:enterprise_key,"
                        "approved_by=CASE WHEN :status='APPROVED' THEN :actor ELSE NULL END,"
                        "approved_at_utc=CASE WHEN :status='APPROVED' "
                        "THEN SYSUTCDATETIME() ELSE NULL END,decision_reason=:reason "
                        "WHERE crosswalk_id=:crosswalk"
                    ),
                    {
                        "status": status,
                        "enterprise_key": enterprise_key,
                        "actor": principal.user_id,
                        "reason": reason,
                        "crosswalk": crosswalk_id,
                    },
                ).rowcount
                if updated != 1:
                    raise DomainError(
                        "CROSSWALK_STATE_CONFLICT",
                        "主数据映射审批时状态已变化",
                        409,
                    )
                connection.execute(
                    text(
                        "UPDATE mdm.product SET identity_class=CASE WHEN EXISTS("
                        "SELECT 1 FROM mdm.enterprise_product_crosswalk cw "
                        "WHERE cw.product_id=:product AND cw.status='APPROVED') "
                        "THEN 'ENTERPRISE_MAPPED' ELSE 'SOURCE_OBSERVED' END "
                        "WHERE product_id=:product"
                    ),
                    {"product": int(before["product_id"])},
                )
                connection.execute(
                    text(
                        "INSERT governance.audit_log(actor,actor_user_id,operation,"
                        "entity_type,entity_id,before_json,after_json,reason) "
                        "VALUES(:actor,:actor_id,:operation,'PRODUCT_CROSSWALK',"
                        ":entity_id,:before_json,:after_json,:reason)"
                    ),
                    {
                        "actor": principal.login_name,
                        "actor_id": principal.user_id,
                        "operation": f"PRODUCT_CROSSWALK_{status}",
                        "entity_id": str(crosswalk_id),
                        "before_json": json.dumps(dict(before), ensure_ascii=False),
                        "after_json": json.dumps(
                            {
                                "status": status,
                                "enterprise_system": "SAP_B1",
                                "enterprise_key": enterprise_key,
                            },
                            ensure_ascii=False,
                        ),
                        "reason": reason,
                    },
                )
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "CROSSWALK_DECISION_UNAVAILABLE",
                "主数据映射审批暂时不可用",
                503,
            ) from exc
        return self._get(crosswalk_id)

    def _get(self, crosswalk_id: int) -> ProductCrosswalk:
        with self._engine.connect() as connection:
            full = (
                connection.execute(
                    text(
                        f"SELECT {_CROSSWALK_COLUMNS} FROM "
                        "mdm.enterprise_product_crosswalk cw "
                        "JOIN mdm.supplier s ON s.supplier_id=cw.supplier_id "
                        "JOIN mdm.product p ON p.product_id=cw.product_id "
                        "LEFT JOIN iam.app_user u ON u.user_id=cw.approved_by "
                        "WHERE cw.crosswalk_id=:crosswalk"
                    ),
                    {"crosswalk": crosswalk_id},
                )
                .mappings()
                .one()
            )
        return _to_crosswalk(full)


def _escape_like(value: str) -> str:
    return value.replace("[", "[[]").replace("%", "[%]").replace("_", "[_]")
