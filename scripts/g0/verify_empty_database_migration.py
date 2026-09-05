from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "db" / "alembic" / "alembic.ini"
SAFE_SCRATCH_DATABASE = re.compile(
    r"^TMS_AUTH_\d{8}_[A-F0-9]{8}_MIGRATION_TEST$"
)


def main() -> int:
    database_url = os.getenv("TMS_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("TMS_DATABASE_URL is required")
    source_url = make_url(database_url)
    database_name = (
        "TMS_AUTH_20260901_"
        f"{secrets.token_hex(4).upper()}_MIGRATION_TEST"
    )
    if SAFE_SCRATCH_DATABASE.fullmatch(database_name) is None:
        raise RuntimeError("generated migration database name is unsafe")

    master_engine = create_engine(
        source_url.set(database="master"),
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    scratch_engine = None
    created = False
    dropped = False
    previous_database_url = os.getenv("TMS_DATABASE_URL")
    try:
        with master_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT COUNT(*) FROM sys.databases WHERE name=:name"),
                {"name": database_name},
            ).scalar_one()
            if int(exists) != 0:
                raise RuntimeError("random migration database already exists")
            connection.exec_driver_sql(f"CREATE DATABASE [{database_name}]")
            connection.exec_driver_sql(
                f"ALTER DATABASE [{database_name}] SET COMPATIBILITY_LEVEL = 120"
            )
            created = True

        scratch_url = source_url.set(database=database_name)
        os.environ["TMS_DATABASE_URL"] = scratch_url.render_as_string(
            hide_password=False
        )
        command.upgrade(Config(str(ALEMBIC_INI)), "head")
        scratch_engine = create_engine(scratch_url, pool_pre_ping=True)
        with scratch_engine.connect() as connection:
            revision = str(
                connection.execute(
                    text("SELECT version_num FROM dbo.alembic_version")
                ).scalar_one()
            )
            if revision != "sql2014_0027":
                raise RuntimeError(f"unexpected scratch schema revision: {revision}")
            required_objects = {
                "test.cp_run_detail",
                "test.ft_run_detail",
                "iam.data_domain",
                "iam.data_domain_grant",
                "ingestion.source_definition",
                "workspace.analysis_session",
            }
            found = {
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT SCHEMA_NAME(schema_id)+'.'+name FROM sys.tables"
                    )
                ).all()
            }
            missing = required_objects - found
            if missing:
                raise RuntimeError(
                    "scratch migration is missing objects: " + ", ".join(sorted(missing))
                )
            manifest_check = str(
                connection.execute(
                    text(
                        "SELECT definition FROM sys.check_constraints "
                        "WHERE parent_object_id=OBJECT_ID('workspace.analysis_session') "
                        "AND name='CK_analysis_session_manifest_mode'"
                    )
                ).scalar_one()
            )
            if "LOCAL_PATH_SIZE_MTIME_V1" not in manifest_check:
                raise RuntimeError("scratch Local Agent manifest contract is missing")
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "database": database_name,
                    "schema_revision": revision,
                    "required_object_count": len(required_objects),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        if previous_database_url is None:
            os.environ.pop("TMS_DATABASE_URL", None)
        else:
            os.environ["TMS_DATABASE_URL"] = previous_database_url
        if scratch_engine is not None:
            scratch_engine.dispose()
        if created:
            with master_engine.connect() as connection:
                exists = int(
                    connection.execute(
                        text("SELECT COUNT(*) FROM sys.databases WHERE name=:name"),
                        {"name": database_name},
                    ).scalar_one()
                )
                if exists:
                    if SAFE_SCRATCH_DATABASE.fullmatch(database_name) is None:
                        raise RuntimeError("refusing to drop an unsafe database name")
                    connection.exec_driver_sql(
                        f"ALTER DATABASE [{database_name}] "
                        "SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
                    )
                    connection.exec_driver_sql(f"DROP DATABASE [{database_name}]")
                    dropped = True
        master_engine.dispose()
        if created and not dropped:
            raise RuntimeError("scratch migration database cleanup did not complete")


if __name__ == "__main__":
    raise SystemExit(main())
