from __future__ import annotations

import argparse
import getpass
import os
import re
from pathlib import Path

import pyodbc
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "db" / "alembic" / "alembic.ini"
SAFE_DATABASE = re.compile(r"^TMS_[A-Z0-9_]{1,48}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an isolated TMS development database and migrate it"
    )
    parser.add_argument("--server", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", default="TMS_G0_DEV")
    parser.add_argument("--driver", default="ODBC Driver 18 for SQL Server")
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Allow migration when the isolated database already exists",
    )
    return parser.parse_args()


def pyodbc_connection_string(args: argparse.Namespace, password: str) -> str:
    return (
        f"DRIVER={{{args.driver}}};SERVER={args.server};DATABASE=master;"
        f"UID={args.user};PWD={password};Encrypt=no;"
        "TrustServerCertificate=yes;Connection Timeout=8"
    )


def ensure_database(args: argparse.Namespace, password: str) -> None:
    if not SAFE_DATABASE.fullmatch(args.database):
        raise ValueError("Database name must match TMS_[A-Z0-9_]{1,48}.")
    connection = pyodbc.connect(
        pyodbc_connection_string(args, password), autocommit=True
    )
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT 1 FROM sys.databases WHERE name = ?", args.database
        )
        exists = cursor.fetchone() is not None
        if exists and not args.reuse:
            raise RuntimeError(
                f"Database {args.database} already exists; use --reuse after review."
            )
        if not exists:
            cursor.execute(f"CREATE DATABASE [{args.database}]")
            cursor.execute(
                f"ALTER DATABASE [{args.database}] SET COMPATIBILITY_LEVEL = 120"
            )
            print(f"created_database={args.database}")
        else:
            print(f"reusing_database={args.database}")
    finally:
        connection.close()


def migrate(args: argparse.Namespace, password: str) -> None:
    host, separator, port_text = args.server.partition(",")
    port = int(port_text) if separator else None
    url = URL.create(
        "mssql+pyodbc",
        username=args.user,
        password=password,
        host=host,
        port=port,
        database=args.database,
        query={
            "driver": args.driver,
            "Encrypt": "no",
            "TrustServerCertificate": "yes",
        },
    )
    previous = os.environ.get("TMS_DATABASE_URL")
    os.environ["TMS_DATABASE_URL"] = url.render_as_string(hide_password=False)
    try:
        config = Config(str(ALEMBIC_INI))
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("TMS_DATABASE_URL", None)
        else:
            os.environ["TMS_DATABASE_URL"] = previous


def main() -> None:
    args = parse_args()
    password = getpass.getpass("SQL password: ")
    ensure_database(args, password)
    migrate(args, password)
    print("migration_status=PASS")


if __name__ == "__main__":
    main()
