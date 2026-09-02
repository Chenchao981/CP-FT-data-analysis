from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence

from sqlalchemy import create_engine, text


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Map explicitly approved historical SERVER_CATALOG Quick sessions "
            "from MIGRATION_HOLD to one active data domain."
        )
    )
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--source-root-code", required=True)
    parser.add_argument("--test-stage", choices=("CP", "FT"), required=True)
    parser.add_argument("--factory-code", required=True)
    parser.add_argument("--data-domain-code", required=True)
    parser.add_argument(
        "--expected-session-ids",
        required=True,
        help="Comma-separated exact session ids approved for this mapping.",
    )
    parser.add_argument("--actor", default="g0:quick-domain-delta")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def _expected_ids(raw: str) -> tuple[int, ...]:
    try:
        values = tuple(
            sorted({int(item.strip()) for item in raw.split(",") if item.strip()})
        )
    except ValueError as exc:
        raise ValueError("expected session ids must be positive integers") from exc
    if not values or any(value < 1 for value in values):
        raise ValueError("expected session ids must be positive integers")
    return values


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    database_url = os.getenv("TMS_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("TMS_DATABASE_URL is required")
    expected_ids = _expected_ids(args.expected_session_ids)
    parameters = {
        "root": args.source_root_code.strip().upper(),
        "stage": args.test_stage.strip().upper(),
        "factory": args.factory_code.strip().upper(),
        "domain_code": args.data_domain_code.strip().upper(),
    }
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            identity = (
                connection.execute(
                    text(
                        "SELECT DB_NAME() AS database_name,"
                        "(SELECT version_num FROM dbo.alembic_version) AS revision"
                    )
                )
                .mappings()
                .one()
            )
            if str(identity["database_name"]) != args.expected_database:
                raise RuntimeError(
                    "database identity does not match --expected-database"
                )
            if str(identity["revision"]) != "sql2014_0025":
                raise RuntimeError("sql2014_0025 is required")

            domains = (
                connection.execute(
                    text(
                        "SELECT data_domain_id,domain_code,test_stage,factory_code,active "
                        "FROM iam.data_domain WITH (UPDLOCK,HOLDLOCK) "
                        "WHERE domain_code IN(:domain_code,N'MIGRATION_HOLD')"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
            by_code = {str(row["domain_code"]): row for row in domains}
            target = by_code.get(parameters["domain_code"])
            hold = by_code.get("MIGRATION_HOLD")
            if target is None or not bool(target["active"]):
                raise RuntimeError("target data domain does not exist or is inactive")
            if str(target["test_stage"]) != parameters["stage"]:
                raise RuntimeError("target data-domain stage does not match")
            target_factory = str(target["factory_code"] or "").strip().upper()
            if target_factory and target_factory != parameters["factory"]:
                raise RuntimeError("target data-domain factory does not match")
            if hold is None or bool(hold["active"]):
                raise RuntimeError("inactive MIGRATION_HOLD data domain is required")

            rows = (
                connection.execute(
                    text(
                        "SELECT analysis_session_id,data_domain_id,access_scope,"
                        "source_root_code,test_stage,factory_code "
                        "FROM workspace.analysis_session WITH (UPDLOCK,HOLDLOCK) "
                        "WHERE source_root_code=:root AND test_stage=:stage "
                        "AND factory_code=:factory ORDER BY analysis_session_id"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
            actual_ids = tuple(int(row["analysis_session_id"]) for row in rows)
            if actual_ids != expected_ids:
                raise RuntimeError(
                    f"exact session-id gate failed: expected={expected_ids}, actual={actual_ids}"
                )
            allowed_domain_ids = {
                int(hold["data_domain_id"]),
                int(target["data_domain_id"]),
            }
            invalid = [
                int(row["analysis_session_id"])
                for row in rows
                if str(row["access_scope"]) != "DOMAIN"
                or int(row["data_domain_id"]) not in allowed_domain_ids
            ]
            if invalid:
                raise RuntimeError(f"sessions have unexpected ACL bindings: {invalid}")

            pending_ids = tuple(
                int(row["analysis_session_id"])
                for row in rows
                if int(row["data_domain_id"]) == int(hold["data_domain_id"])
            )
            if args.apply:
                for session_id in pending_ids:
                    updated = connection.execute(
                        text(
                            "UPDATE workspace.analysis_session SET data_domain_id=:target "
                            "WHERE analysis_session_id=:session AND access_scope='DOMAIN' "
                            "AND data_domain_id=:hold"
                        ),
                        {
                            "target": int(target["data_domain_id"]),
                            "session": session_id,
                            "hold": int(hold["data_domain_id"]),
                        },
                    )
                    if updated.rowcount != 1:
                        raise RuntimeError(f"session {session_id} changed concurrently")
                    connection.execute(
                        text(
                            "INSERT governance.audit_log(actor,operation,entity_type,"
                            "entity_id,before_json,after_json,reason,actor_user_id) "
                            "VALUES(:actor,'QUICK_DATA_DOMAIN_MAPPED',"
                            "'workspace.analysis_session',:entity,:before_json,"
                            ":after_json,:reason,NULL)"
                        ),
                        {
                            "actor": args.actor[:128],
                            "entity": str(session_id),
                            "before_json": json.dumps(
                                {
                                    "access_scope": "DOMAIN",
                                    "data_domain_code": "MIGRATION_HOLD",
                                },
                                separators=(",", ":"),
                            ),
                            "after_json": json.dumps(
                                {
                                    "access_scope": "DOMAIN",
                                    "data_domain_code": parameters["domain_code"],
                                    "source_root_code": parameters["root"],
                                },
                                separators=(",", ":"),
                            ),
                            "reason": "explicit root/stage/factory historical Quick mapping",
                        },
                    )

            report = {
                "database": str(identity["database_name"]),
                "revision": str(identity["revision"]),
                "mode": "APPLY" if args.apply else "DRY_RUN",
                "source_root_code": parameters["root"],
                "test_stage": parameters["stage"],
                "factory_code": parameters["factory"],
                "data_domain_code": parameters["domain_code"],
                "expected_session_ids": list(expected_ids),
                "pending_before": len(pending_ids),
                "mapped": len(pending_ids) if args.apply else 0,
                "already_mapped": len(expected_ids) - len(pending_ids),
            }
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
