from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, text


def _scalar(connection, statement: str) -> int | str:
    return connection.execute(text(statement)).scalar_one()


def main() -> int:
    database_url = os.getenv("TMS_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("TMS_DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            revision = str(
                _scalar(connection, "SELECT version_num FROM dbo.alembic_version")
            )
            if revision != "sql2014_0028":
                raise RuntimeError(f"sql2014_0028 is required, database is {revision}")

            required_tables = (
                "iam.data_domain",
                "iam.data_domain_grant",
                "ingestion.source_definition",
            )
            missing_tables = [
                name
                for name in required_tables
                if int(
                    connection.execute(
                        text(
                            "SELECT COUNT(*) FROM sys.tables WHERE object_id=OBJECT_ID(:name)"
                        ),
                        {"name": name},
                    ).scalar_one()
                )
                != 1
            ]
            if missing_tables:
                raise RuntimeError(
                    "missing authorization tables: " + ", ".join(missing_tables)
                )

            for table in ("ingestion.import_batch", "dataset.dataset"):
                columns = {
                    str(row[0])
                    for row in connection.execute(
                        text(
                            "SELECT name FROM sys.columns "
                            "WHERE object_id=OBJECT_ID(:table)"
                        ),
                        {"table": table},
                    ).all()
                }
                missing = {
                    "access_scope",
                    "data_domain_id",
                    "source_definition_id",
                } - columns
                if missing:
                    raise RuntimeError(
                        f"{table} is missing authorization columns: {sorted(missing)}"
                    )

            quick_columns = {
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT name FROM sys.columns WHERE "
                        "object_id=OBJECT_ID('workspace.analysis_session')"
                    )
                ).all()
            }
            missing_quick = {"access_scope", "data_domain_id"} - quick_columns
            if missing_quick:
                raise RuntimeError(
                    "workspace.analysis_session is missing authorization columns: "
                    f"{sorted(missing_quick)}"
                )

            manifest_check = str(
                _scalar(
                    connection,
                    "SELECT definition FROM sys.check_constraints "
                    "WHERE parent_object_id=OBJECT_ID('workspace.analysis_session') "
                    "AND name='CK_analysis_session_manifest_mode'",
                )
            )
            if "LOCAL_PATH_SIZE_MTIME_V1" not in manifest_check:
                raise RuntimeError("Local Agent manifest mode is not enabled")

            checks = {
                "invalid_import_batch_binding": (
                    "SELECT COUNT(*) FROM ingestion.import_batch WHERE "
                    "(access_scope='PERSONAL' AND (owner_user_id IS NULL OR "
                    "data_domain_id IS NOT NULL OR source_definition_id IS NOT NULL)) OR "
                    "(access_scope='DOMAIN' AND (owner_user_id IS NULL OR data_domain_id IS NULL)) OR "
                    "access_scope NOT IN('PERSONAL','DOMAIN')"
                ),
                "invalid_dataset_binding": (
                    "SELECT COUNT(*) FROM dataset.dataset WHERE "
                    "(access_scope='PERSONAL' AND (owner_user_id IS NULL OR "
                    "data_domain_id IS NOT NULL OR source_definition_id IS NOT NULL)) OR "
                    "(access_scope='DOMAIN' AND (owner_user_id IS NULL OR data_domain_id IS NULL)) OR "
                    "access_scope NOT IN('PERSONAL','DOMAIN')"
                ),
                "invalid_personal_dataset_batch_lineage": (
                    "SELECT COUNT(*) FROM dataset.dataset d "
                    "JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id "
                    "JOIN ingestion.import_batch b "
                    "ON b.import_batch_id=dv.input_batch_id "
                    "WHERE d.access_scope='PERSONAL' AND ("
                    "b.access_scope<>'PERSONAL' OR b.owner_user_id<>d.owner_user_id "
                    "OR b.data_domain_id IS NOT NULL "
                    "OR b.source_definition_id IS NOT NULL)"
                ),
                "invalid_quick_binding": (
                    "SELECT COUNT(*) FROM workspace.analysis_session WHERE "
                    "(source_root_code='LOCAL_AGENT' AND "
                    "(access_scope<>'PERSONAL' OR data_domain_id IS NOT NULL)) OR "
                    "(source_root_code<>'LOCAL_AGENT' AND "
                    "(access_scope<>'DOMAIN' OR data_domain_id IS NULL)) OR "
                    "access_scope NOT IN('PERSONAL','DOMAIN')"
                ),
                "system_ingestion_active_or_role_bound": (
                    "SELECT COUNT(*) FROM iam.app_user u WHERE "
                    "u.login_name='SYSTEM_INGESTION' AND "
                    "(u.status<>'DISABLED' OR EXISTS("
                    "SELECT 1 FROM iam.user_role ur WHERE ur.user_id=u.user_id))"
                ),
                "system_admin_has_break_glass": (
                    "SELECT COUNT(*) FROM iam.role r "
                    "JOIN iam.role_permission rp ON rp.role_id=r.role_id "
                    "JOIN iam.permission p ON p.permission_id=rp.permission_id "
                    "WHERE r.role_code='SYSTEM_ADMIN' "
                    "AND p.permission_code='DATA_BREAK_GLASS'"
                ),
                "break_glass_role_binding": (
                    "SELECT COUNT(*) FROM iam.role_permission rp "
                    "JOIN iam.permission p ON p.permission_id=rp.permission_id "
                    "WHERE p.permission_code='DATA_BREAK_GLASS'"
                ),
                "break_glass_grantable_role": (
                    "SELECT COUNT(*) FROM iam.role WHERE role_code='DATA_BREAK_GLASS'"
                ),
            }
            failures = {
                name: int(_scalar(connection, statement))
                for name, statement in checks.items()
            }
            nonzero = {name: count for name, count in failures.items() if count}
            if nonzero:
                raise RuntimeError(f"authorization integrity checks failed: {nonzero}")

            counts = {
                "personal_import_batches": int(
                    _scalar(
                        connection,
                        "SELECT COUNT(*) FROM ingestion.import_batch "
                        "WHERE access_scope='PERSONAL'",
                    )
                ),
                "domain_import_batches": int(
                    _scalar(
                        connection,
                        "SELECT COUNT(*) FROM ingestion.import_batch "
                        "WHERE access_scope='DOMAIN'",
                    )
                ),
                "personal_datasets": int(
                    _scalar(
                        connection,
                        "SELECT COUNT(*) FROM dataset.dataset "
                        "WHERE access_scope='PERSONAL'",
                    )
                ),
                "domain_datasets": int(
                    _scalar(
                        connection,
                        "SELECT COUNT(*) FROM dataset.dataset "
                        "WHERE access_scope='DOMAIN'",
                    )
                ),
                "personal_quick_sessions": int(
                    _scalar(
                        connection,
                        "SELECT COUNT(*) FROM workspace.analysis_session "
                        "WHERE access_scope='PERSONAL'",
                    )
                ),
                "domain_quick_sessions": int(
                    _scalar(
                        connection,
                        "SELECT COUNT(*) FROM workspace.analysis_session "
                        "WHERE access_scope='DOMAIN'",
                    )
                ),
                "migration_hold_quick_sessions": int(
                    _scalar(
                        connection,
                        "SELECT COUNT(*) FROM workspace.analysis_session s "
                        "JOIN iam.data_domain d ON d.data_domain_id=s.data_domain_id "
                        "WHERE d.domain_code='MIGRATION_HOLD'",
                    )
                ),
                "domain_quick_sessions_with_live_owner_grant": int(
                    _scalar(
                        connection,
                        "SELECT COUNT(*) FROM workspace.analysis_session s "
                        "JOIN iam.data_domain d ON d.data_domain_id=s.data_domain_id "
                        "WHERE s.access_scope='DOMAIN' AND d.active=1 AND EXISTS("
                        "SELECT 1 FROM iam.data_domain_grant g "
                        "JOIN iam.app_user u ON u.user_id=g.user_id "
                        "WHERE g.data_domain_id=s.data_domain_id "
                        "AND g.user_id=s.owner_user_id AND g.status='ACTIVE' "
                        "AND u.status='ACTIVE' AND (g.expires_at_utc IS NULL "
                        "OR g.expires_at_utc>SYSUTCDATETIME()))",
                    )
                ),
                "quick_acl_schema_delta_audits": int(
                    _scalar(
                        connection,
                        "SELECT COUNT(*) FROM governance.audit_log WHERE "
                        "operation='QUICK_SESSION_ACL_SCHEMA_DELTA' AND "
                        "correlation_id="
                        "'quick-session-acl-schema-delta:sql2014_0028'",
                    )
                ),
                "quick_domain_mapping_audits": int(
                    _scalar(
                        connection,
                        "SELECT COUNT(*) FROM governance.audit_log WHERE "
                        "operation='QUICK_DATA_DOMAIN_MAPPED' AND "
                        "entity_type='workspace.analysis_session'",
                    )
                ),
                "active_data_domains": int(
                    _scalar(
                        connection,
                        "SELECT COUNT(*) FROM iam.data_domain WHERE active=1",
                    )
                ),
                "active_data_domain_grants": int(
                    _scalar(
                        connection,
                        "SELECT COUNT(*) FROM iam.data_domain_grant "
                        "WHERE status='ACTIVE' AND "
                        "(expires_at_utc IS NULL OR expires_at_utc>SYSUTCDATETIME())",
                    )
                ),
            }
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "schema_revision": revision,
                        "integrity_failures": failures,
                        "counts": counts,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
