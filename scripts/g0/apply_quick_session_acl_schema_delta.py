from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Connection, create_engine, text

EXPECTED_REVISION = "sql2014_0024"
TABLE_NAME = "workspace.analysis_session"
LOCK_RESOURCE = "TMS:workspace.analysis_session:personal-domain-acl-schema-delta"
AUDIT_CORRELATION = "quick-session-acl-schema-delta:sql2014_0024"
AUDIT_ACTOR = "script:apply_quick_session_acl_schema_delta"

ACCESS_SCOPE_CHECK = (
    "access_scope='personal'oraccess_scope='domain'",
    "access_scope='domain'oraccess_scope='personal'",
    "access_scopein'personal','domain'",
    "access_scopein'domain','personal'",
)
ACCESS_BINDING_CHECK = (
    "source_root_code='local_agent'andaccess_scope='personal'and"
    "data_domain_idisnullorsource_root_code<>'local_agent'and"
    "access_scope='domain'anddata_domain_idisnotnull"
)
INDEX_KEYS = (
    ("data_domain_id", False),
    ("created_at_utc", True),
    ("analysis_session_id", True),
)
INDEX_INCLUDES = frozenset(
    {
        "access_scope",
        "owner_user_id",
        "status",
        "analysis_type",
        "test_stage",
        "factory_code",
        "source_file_count",
        "source_total_bytes",
        "expires_at_utc",
    }
)


class SchemaDeltaError(RuntimeError):
    """Expected, sanitized failure from the schema-delta safety gates."""


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Safely add and audit the Quick Analysis PERSONAL/DOMAIN ACL columns "
            "when a development database is already at sql2014_0024."
        )
    )
    parser.add_argument("--expected-database", default="TMS_G0_DEV")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the delta. Without this flag the transaction is rolled back.",
    )
    return parser.parse_args(argv)


def _identity(connection: Connection) -> dict[str, str]:
    row = (
        connection.execute(
            text(
                "SELECT DB_NAME() AS database_name,"
                "(SELECT version_num FROM dbo.alembic_version) AS revision"
            )
        )
        .mappings()
        .one()
    )
    return {
        "database_name": str(row["database_name"]),
        "revision": str(row["revision"]),
    }


def _require_identity(
    connection: Connection, *, expected_database: str
) -> dict[str, str]:
    identity = _identity(connection)
    if identity["database_name"] != expected_database:
        raise SchemaDeltaError("database identity does not match --expected-database")
    if identity["revision"] != EXPECTED_REVISION:
        raise SchemaDeltaError(f"{EXPECTED_REVISION} is required")
    table_exists = int(
        connection.execute(
            text("SELECT COUNT(*) FROM sys.tables WHERE object_id=OBJECT_ID(:table)"),
            {"table": TABLE_NAME},
        ).scalar_one()
    )
    if table_exists != 1:
        raise SchemaDeltaError(f"required table {TABLE_NAME} does not exist")
    return identity


def _acquire_transaction_lock(connection: Connection) -> None:
    lock_result = int(
        connection.execute(
            text(
                "SET NOCOUNT ON; "
                "DECLARE @result int; "
                "EXEC @result=sys.sp_getapplock "
                "@Resource=:resource,@LockMode='Exclusive',"
                "@LockOwner='Transaction',@LockTimeout=15000; "
                "SELECT @result AS lock_result;"
            ),
            {"resource": LOCK_RESOURCE},
        ).scalar_one()
    )
    if lock_result < 0:
        raise SchemaDeltaError("could not acquire the schema-delta transaction lock")


def _column_metadata(connection: Connection) -> dict[str, dict[str, Any]]:
    rows = (
        connection.execute(
            text(
                "SELECT c.name,t.name AS data_type,t.is_user_defined,"
                "c.max_length,c.precision,c.scale,c.is_nullable,c.is_computed,"
                "c.default_object_id,c.rule_object_id "
                "FROM sys.columns c "
                "JOIN sys.types t ON t.user_type_id=c.user_type_id "
                "WHERE c.object_id=OBJECT_ID(:table) "
                "AND c.name IN('access_scope','data_domain_id')"
            ),
            {"table": TABLE_NAME},
        )
        .mappings()
        .all()
    )
    return {str(row["name"]): dict(row) for row in rows}


def _schema_state(columns: Mapping[str, Mapping[str, Any]]) -> str:
    present = frozenset(columns)
    expected = frozenset({"access_scope", "data_domain_id"})
    if not present:
        return "ABSENT"
    if present == expected:
        return "PRESENT"
    raise SchemaDeltaError(
        "partial Quick ACL schema detected; exactly one required column exists"
    )


def _migration_hold_id(connection: Connection) -> int:
    rows = (
        connection.execute(
            text(
                "SELECT data_domain_id,active FROM iam.data_domain "
                "WITH (UPDLOCK,HOLDLOCK) WHERE domain_code=N'MIGRATION_HOLD'"
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise SchemaDeltaError("exactly one MIGRATION_HOLD data domain is required")
    if bool(rows[0]["active"]):
        raise SchemaDeltaError("MIGRATION_HOLD data domain must be inactive")
    return int(rows[0]["data_domain_id"])


def _session_counts(connection: Connection) -> dict[str, int]:
    row = (
        connection.execute(
            text(
                "SELECT COUNT_BIG(*) AS total,"
                "SUM(CASE WHEN source_root_code=N'LOCAL_AGENT' THEN 1 ELSE 0 END) "
                "AS local_count,"
                "SUM(CASE WHEN source_root_code<>N'LOCAL_AGENT' THEN 1 ELSE 0 END) "
                "AS server_count "
                "FROM workspace.analysis_session WITH (UPDLOCK,HOLDLOCK)"
            )
        )
        .mappings()
        .one()
    )
    return {
        "total": int(row["total"] or 0),
        "personal_backfill": int(row["local_count"] or 0),
        "migration_hold_backfill": int(row["server_count"] or 0),
    }


def _require_names_available(connection: Connection) -> None:
    constraint_rows = connection.execute(
        text(
            "SELECT o.name FROM sys.objects o "
            "WHERE o.schema_id=SCHEMA_ID(N'workspace') AND o.name IN("
            "N'FK_analysis_session_data_domain',"
            "N'CK_analysis_session_access_scope',"
            "N'CK_analysis_session_access_binding')"
        )
    ).all()
    index_rows = connection.execute(
        text(
            "SELECT i.name FROM sys.indexes i "
            "WHERE i.object_id=OBJECT_ID(:table) "
            "AND i.name=N'IX_analysis_session_domain_access'"
        ),
        {"table": TABLE_NAME},
    ).all()
    if constraint_rows or index_rows:
        raise SchemaDeltaError(
            "ACL object-name collision exists while both ACL columns are absent"
        )


def _normalize_check_definition(value: str) -> str:
    normalized = value.lower().replace("[", "").replace("]", "")
    normalized = re.sub(r"\s+", "", normalized)
    normalized = normalized.replace("(", "").replace(")", "")
    return re.sub(r"(?<![a-z0-9_])n'", "'", normalized)


def _validate_columns(columns: Mapping[str, Mapping[str, Any]]) -> None:
    access = columns["access_scope"]
    access_valid = (
        str(access["data_type"]).lower() == "varchar"
        and not bool(access["is_user_defined"])
        and int(access["max_length"]) == 16
        and not bool(access["is_nullable"])
        and not bool(access["is_computed"])
        and int(access["default_object_id"]) == 0
        and int(access["rule_object_id"]) == 0
    )
    domain = columns["data_domain_id"]
    domain_valid = (
        str(domain["data_type"]).lower() == "bigint"
        and not bool(domain["is_user_defined"])
        and int(domain["max_length"]) == 8
        and bool(domain["is_nullable"])
        and not bool(domain["is_computed"])
        and int(domain["default_object_id"]) == 0
        and int(domain["rule_object_id"]) == 0
    )
    if not access_valid or not domain_valid:
        raise SchemaDeltaError("Quick ACL column definitions do not match the contract")


def _validate_foreign_key(connection: Connection) -> None:
    rows = (
        connection.execute(
            text(
                "SELECT fk.is_disabled,fk.is_not_trusted,"
                "fk.delete_referential_action,fk.update_referential_action,"
                "pc.name AS parent_column,SCHEMA_NAME(ro.schema_id) AS ref_schema,"
                "ro.name AS ref_table,rc.name AS ref_column "
                "FROM sys.foreign_keys fk "
                "JOIN sys.foreign_key_columns fkc "
                "ON fkc.constraint_object_id=fk.object_id "
                "JOIN sys.columns pc ON pc.object_id=fkc.parent_object_id "
                "AND pc.column_id=fkc.parent_column_id "
                "JOIN sys.objects ro ON ro.object_id=fkc.referenced_object_id "
                "JOIN sys.columns rc ON rc.object_id=fkc.referenced_object_id "
                "AND rc.column_id=fkc.referenced_column_id "
                "WHERE fk.parent_object_id=OBJECT_ID(:table) "
                "AND fk.name=N'FK_analysis_session_data_domain'"
            ),
            {"table": TABLE_NAME},
        )
        .mappings()
        .all()
    )
    expected = {
        "parent_column": "data_domain_id",
        "ref_schema": "iam",
        "ref_table": "data_domain",
        "ref_column": "data_domain_id",
    }
    if len(rows) != 1:
        raise SchemaDeltaError(
            "Quick ACL foreign key is missing or has multiple columns"
        )
    row = rows[0]
    actual = {key: str(row[key]) for key in expected}
    healthy = (
        actual == expected
        and not bool(row["is_disabled"])
        and not bool(row["is_not_trusted"])
        and int(row["delete_referential_action"]) == 0
        and int(row["update_referential_action"]) == 0
    )
    if not healthy:
        raise SchemaDeltaError("Quick ACL foreign key does not match the contract")


def _validate_checks(connection: Connection) -> None:
    rows = (
        connection.execute(
            text(
                "SELECT name,definition,is_disabled,is_not_trusted "
                "FROM sys.check_constraints "
                "WHERE parent_object_id=OBJECT_ID(:table) AND name IN("
                "N'CK_analysis_session_access_scope',"
                "N'CK_analysis_session_access_binding')"
            ),
            {"table": TABLE_NAME},
        )
        .mappings()
        .all()
    )
    by_name = {str(row["name"]): row for row in rows}
    if set(by_name) != {
        "CK_analysis_session_access_scope",
        "CK_analysis_session_access_binding",
    }:
        raise SchemaDeltaError("Quick ACL check constraints are incomplete")
    for row in by_name.values():
        if bool(row["is_disabled"]) or bool(row["is_not_trusted"]):
            raise SchemaDeltaError(
                "Quick ACL check constraints must be enabled and trusted"
            )
    scope_definition = _normalize_check_definition(
        str(by_name["CK_analysis_session_access_scope"]["definition"])
    )
    binding_definition = _normalize_check_definition(
        str(by_name["CK_analysis_session_access_binding"]["definition"])
    )
    if scope_definition not in ACCESS_SCOPE_CHECK:
        raise SchemaDeltaError("Quick ACL scope check does not match the contract")
    if binding_definition != ACCESS_BINDING_CHECK:
        raise SchemaDeltaError("Quick ACL binding check does not match the contract")


def _validate_index(connection: Connection) -> None:
    rows = (
        connection.execute(
            text(
                "SELECT i.type_desc,i.is_unique,i.is_disabled,i.is_hypothetical,"
                "i.has_filter,i.filter_definition,ic.key_ordinal,"
                "ic.index_column_id,ic.is_included_column,ic.is_descending_key,"
                "c.name AS column_name "
                "FROM sys.indexes i "
                "JOIN sys.index_columns ic ON ic.object_id=i.object_id "
                "AND ic.index_id=i.index_id "
                "JOIN sys.columns c ON c.object_id=ic.object_id "
                "AND c.column_id=ic.column_id "
                "WHERE i.object_id=OBJECT_ID(:table) "
                "AND i.name=N'IX_analysis_session_domain_access' "
                "ORDER BY ic.index_column_id"
            ),
            {"table": TABLE_NAME},
        )
        .mappings()
        .all()
    )
    if not rows:
        raise SchemaDeltaError("Quick ACL access index is missing")
    first = rows[0]
    healthy = (
        str(first["type_desc"]) == "NONCLUSTERED"
        and not bool(first["is_unique"])
        and not bool(first["is_disabled"])
        and not bool(first["is_hypothetical"])
        and not bool(first["has_filter"])
        and first["filter_definition"] is None
    )
    keys = tuple(
        (str(row["column_name"]), bool(row["is_descending_key"]))
        for row in sorted(rows, key=lambda item: int(item["key_ordinal"]))
        if not bool(row["is_included_column"])
    )
    includes = frozenset(
        str(row["column_name"]) for row in rows if bool(row["is_included_column"])
    )
    if not healthy or keys != INDEX_KEYS or includes != INDEX_INCLUDES:
        raise SchemaDeltaError("Quick ACL access index does not match the contract")


def _invalid_binding_count(connection: Connection) -> int:
    return int(
        connection.execute(
            text(
                "SELECT COUNT_BIG(*) FROM workspace.analysis_session s "
                "WHERE s.access_scope IS NULL "
                "OR s.access_scope NOT IN('PERSONAL','DOMAIN') "
                "OR (s.source_root_code=N'LOCAL_AGENT' AND "
                "(s.access_scope<>'PERSONAL' OR s.data_domain_id IS NOT NULL)) "
                "OR (s.source_root_code<>N'LOCAL_AGENT' AND "
                "(s.access_scope<>'DOMAIN' OR s.data_domain_id IS NULL)) "
                "OR (s.data_domain_id IS NOT NULL AND NOT EXISTS("
                "SELECT 1 FROM iam.data_domain d "
                "WHERE d.data_domain_id=s.data_domain_id))"
            )
        ).scalar_one()
    )


def _validate_present_schema(connection: Connection) -> None:
    columns = _column_metadata(connection)
    if _schema_state(columns) != "PRESENT":
        raise SchemaDeltaError("Quick ACL schema is not complete")
    _validate_columns(columns)
    _validate_foreign_key(connection)
    _validate_checks(connection)
    _validate_index(connection)
    invalid_bindings = _invalid_binding_count(connection)
    if invalid_bindings:
        raise SchemaDeltaError(
            f"Quick ACL fail-closed binding validation found {invalid_bindings} rows"
        )


def _apply_delta(connection: Connection, *, hold_domain_id: int) -> None:
    connection.execute(
        text(
            "ALTER TABLE workspace.analysis_session ADD "
            "access_scope varchar(16) NULL,data_domain_id bigint NULL"
        )
    )
    connection.execute(
        text(
            "UPDATE workspace.analysis_session SET "
            "access_scope=CASE WHEN source_root_code=N'LOCAL_AGENT' "
            "THEN 'PERSONAL' ELSE 'DOMAIN' END,"
            "data_domain_id=CASE WHEN source_root_code=N'LOCAL_AGENT' "
            "THEN NULL ELSE :hold END"
        ),
        {"hold": hold_domain_id},
    )
    invalid_bindings = _invalid_binding_count(connection)
    if invalid_bindings:
        raise SchemaDeltaError(
            f"Quick ACL backfill failed closed for {invalid_bindings} rows"
        )
    connection.execute(
        text(
            "ALTER TABLE workspace.analysis_session "
            "ALTER COLUMN access_scope varchar(16) NOT NULL"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE workspace.analysis_session WITH CHECK ADD CONSTRAINT "
            "FK_analysis_session_data_domain FOREIGN KEY(data_domain_id) "
            "REFERENCES iam.data_domain(data_domain_id)"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE workspace.analysis_session CHECK CONSTRAINT "
            "FK_analysis_session_data_domain"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE workspace.analysis_session WITH CHECK ADD CONSTRAINT "
            "CK_analysis_session_access_scope "
            "CHECK(access_scope IN('PERSONAL','DOMAIN'))"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE workspace.analysis_session CHECK CONSTRAINT "
            "CK_analysis_session_access_scope"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE workspace.analysis_session WITH CHECK ADD CONSTRAINT "
            "CK_analysis_session_access_binding CHECK("
            "(source_root_code=N'LOCAL_AGENT' AND access_scope='PERSONAL' "
            "AND data_domain_id IS NULL) OR "
            "(source_root_code<>N'LOCAL_AGENT' AND access_scope='DOMAIN' "
            "AND data_domain_id IS NOT NULL))"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE workspace.analysis_session CHECK CONSTRAINT "
            "CK_analysis_session_access_binding"
        )
    )
    connection.execute(
        text(
            "CREATE NONCLUSTERED INDEX IX_analysis_session_domain_access "
            "ON workspace.analysis_session("
            "data_domain_id,created_at_utc DESC,analysis_session_id DESC) "
            "INCLUDE(access_scope,owner_user_id,status,analysis_type,test_stage,"
            "factory_code,source_file_count,source_total_bytes,expires_at_utc)"
        )
    )


def _write_audit(
    connection: Connection,
    *,
    state_before: str,
    counts: Mapping[str, int],
    changed: bool,
) -> tuple[bool, bool]:
    existing_audits = int(
        connection.execute(
            text(
                "SELECT COUNT_BIG(*) FROM governance.audit_log "
                "WITH (UPDLOCK,HOLDLOCK) WHERE correlation_id=:correlation "
                "AND operation='QUICK_SESSION_ACL_SCHEMA_DELTA'"
            ),
            {"correlation": AUDIT_CORRELATION},
        ).scalar_one()
    )
    if existing_audits > 1:
        raise SchemaDeltaError("schema-delta audit exists more than once")
    if existing_audits == 1 and changed:
        raise SchemaDeltaError(
            "schema-delta audit already exists while the ACL schema was absent"
        )

    before_json = json.dumps(
        {"schema_state": state_before, "session_counts": dict(counts)},
        separators=(",", ":"),
        sort_keys=True,
    )
    after_json = json.dumps(
        {
            "schema_state": "PRESENT",
            "access_scope": "varchar(16) NOT NULL",
            "data_domain_id": "bigint NULL",
            "constraints_trusted": True,
            "index": "IX_analysis_session_domain_access",
            "changed": changed,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    inserted_audit_id = connection.execute(
        text(
            "INSERT governance.audit_log(actor,operation,entity_type,entity_id,"
            "before_json,after_json,reason,correlation_id) "
            "OUTPUT inserted.audit_id "
            "SELECT :actor,:operation,:entity_type,:entity_id,:before_json,"
            ":after_json,:reason,:correlation "
            "WHERE NOT EXISTS(SELECT 1 FROM governance.audit_log WITH (UPDLOCK,HOLDLOCK) "
            "WHERE correlation_id=:correlation AND operation=:operation)"
        ),
        {
            "actor": AUDIT_ACTOR,
            "operation": "QUICK_SESSION_ACL_SCHEMA_DELTA",
            "entity_type": TABLE_NAME,
            "entity_id": TABLE_NAME,
            "before_json": before_json,
            "after_json": after_json,
            "reason": (
                "Backfill or verify the post-sql2014_0024 Quick Analysis "
                "PERSONAL/DOMAIN authorization boundary"
            ),
            "correlation": AUDIT_CORRELATION,
        },
    ).scalar_one_or_none()
    written = inserted_audit_id is not None
    audit_count = int(
        connection.execute(
            text(
                "SELECT COUNT_BIG(*) FROM governance.audit_log "
                "WHERE correlation_id=:correlation "
                "AND operation='QUICK_SESSION_ACL_SCHEMA_DELTA'"
            ),
            {"correlation": AUDIT_CORRELATION},
        ).scalar_one()
    )
    if audit_count != 1:
        raise SchemaDeltaError("schema-delta audit must exist exactly once")
    return written, not written


def run(argv: Sequence[str] | None = None) -> dict[str, object]:
    args = _arguments(argv)
    expected_database = str(args.expected_database).strip()
    if not expected_database:
        raise SchemaDeltaError("--expected-database must not be empty")
    database_url = os.getenv("TMS_DATABASE_URL", "").strip()
    if not database_url:
        raise SchemaDeltaError("TMS_DATABASE_URL is required")

    engine = create_engine(database_url, pool_pre_ping=True)
    connection: Connection | None = None
    transaction = None
    try:
        connection = engine.connect()
        transaction = connection.begin()
        connection.execute(text("SET XACT_ABORT ON"))
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
        identity = _require_identity(connection, expected_database=expected_database)
        _acquire_transaction_lock(connection)
        identity = _require_identity(connection, expected_database=expected_database)

        columns = _column_metadata(connection)
        state_before = _schema_state(columns)
        hold_domain_id = _migration_hold_id(connection)
        counts = _session_counts(connection)
        changed = False
        audit_written = False
        audit_preexisting = False

        if state_before == "ABSENT":
            _require_names_available(connection)
            if args.apply:
                _apply_delta(connection, hold_domain_id=hold_domain_id)
                changed = True
                _validate_present_schema(connection)
        else:
            _validate_present_schema(connection)

        if args.apply:
            audit_written, audit_preexisting = _write_audit(
                connection,
                state_before=state_before,
                counts=counts,
                changed=changed,
            )
            _validate_present_schema(connection)
            transaction.commit()
        else:
            transaction.rollback()

        return {
            "status": "PASS",
            "database": identity["database_name"],
            "revision": identity["revision"],
            "mode": "APPLY" if args.apply else "DRY_RUN",
            "schema_state_before": state_before,
            "change_required": state_before == "ABSENT",
            "schema_changed": changed,
            "session_counts": counts,
            "migration_hold_verified_inactive": True,
            "validation": "STRICT_PASS"
            if state_before == "PRESENT" or changed
            else "PLAN_PASS",
            "audit_written": audit_written,
            "audit_preexisting": audit_preexisting,
        }
    finally:
        if transaction is not None and transaction.is_active:
            transaction.rollback()
        if connection is not None:
            connection.close()
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    try:
        report = run(argv)
    except SchemaDeltaError as exc:
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "error_code": "SAFETY_GATE_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001 - database details must remain suppressed
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "error_code": "DATABASE_OPERATION_FAILED",
                    "error_type": type(exc).__name__,
                    "message": "database operation failed; sensitive details suppressed",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
