from __future__ import annotations

import argparse
import getpass
import sys

import pyodbc


MINIMUM_SP3_BUILD = (12, 0, 6024, 0)


def version_tuple(value: str) -> tuple[int, int, int, int]:
    parts = tuple(int(item) for item in value.split("."))
    if len(parts) != 4:
        raise ValueError(f"unexpected SQL Server version: {value}")
    return parts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Revalidate SQL Server 2014 Enterprise after installation"
    )
    parser.add_argument("--server", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--driver", default="ODBC Driver 18 for SQL Server")
    args = parser.parse_args()

    password = getpass.getpass("SQL password: ")
    connection = pyodbc.connect(
        (
            f"DRIVER={{{args.driver}}};SERVER={args.server};DATABASE=master;"
            f"UID={args.user};PWD={password};Encrypt=no;"
            "TrustServerCertificate=yes;Connection Timeout=8"
        ),
        autocommit=True,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)),
                CAST(SERVERPROPERTY('ProductLevel') AS nvarchar(128)),
                CAST(SERVERPROPERTY('Edition') AS nvarchar(256)),
                CAST(SERVERPROPERTY('EngineEdition') AS int),
                CAST(SERVERPROPERTY('Collation') AS nvarchar(128)),
                HAS_PERMS_BY_NAME(NULL, NULL, 'CREATE ANY DATABASE')
            """
        )
        row = cursor.fetchone()
        product_version = row[0]
        product_level = row[1]
        edition = row[2]
        engine_edition = row[3]
        collation = row[4]
        can_create_database = bool(row[5])

        failures: list[str] = []
        if not product_version.startswith("12."):
            failures.append(f"expected SQL Server 2014 (12.x), got {product_version}")
        if "Enterprise" not in edition:
            failures.append(f"expected Enterprise edition, got {edition}")
        if version_tuple(product_version) < MINIMUM_SP3_BUILD:
            failures.append(
                "SQL Server 2014 SP3 or later update is required; "
                f"current build is {product_version} ({product_level})"
            )
        if not can_create_database:
            failures.append("current migration login cannot create an isolated database")

        print(f"product_version={product_version}")
        print(f"product_level={product_level}")
        print(f"edition={edition}")
        print(f"engine_edition={engine_edition}")
        print(f"collation={collation}")
        print(f"can_create_database={can_create_database}")
        if failures:
            for failure in failures:
                print(f"FAIL: {failure}", file=sys.stderr)
            raise SystemExit(2)
        print("enterprise_revalidation=PASS")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
