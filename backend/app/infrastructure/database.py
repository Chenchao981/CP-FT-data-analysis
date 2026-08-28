from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    database_url = os.environ.get("TMS_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TMS_DATABASE_URL is not configured")
    options: dict[str, object] = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }
    if database_url.lower().startswith("mssql+pyodbc"):
        options["fast_executemany"] = True
    return create_engine(database_url, **options)


def check_database() -> dict[str, str]:
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)), "
                "DB_NAME(), (SELECT version_num FROM alembic_version), "
                "CAST(SERVERPROPERTY('ServerName') AS nvarchar(256))"
            )
        ).one()
    return {
        "database": str(row[1]),
        "database_version": str(row[0]),
        "schema_revision": str(row[2]),
        "database_server": str(row[3]),
    }
