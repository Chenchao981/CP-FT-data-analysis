from __future__ import annotations

import argparse
import getpass
import json

import pyodbc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only SQL Server G0 inventory")
    parser.add_argument("--server", required=True, help="SQL Server host or host,port")
    parser.add_argument("--user", required=True, help="SQL login used only for this session")
    parser.add_argument("--driver", default="ODBC Driver 18 for SQL Server")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    password = getpass.getpass("SQL password: ")
    connection_string = (
        f"DRIVER={{{args.driver}}};"
        f"SERVER={args.server};DATABASE=master;UID={args.user};PWD={password};"
        "Encrypt=no;TrustServerCertificate=yes;Connection Timeout=8"
    )
    connection = pyodbc.connect(connection_string, autocommit=True)
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
                CAST(SERVERPROPERTY('IsClustered') AS int),
                @@VERSION
            """
        )
        server = cursor.fetchone()
        cursor.execute(
            "SELECT windows_release, windows_service_pack_level "
            "FROM sys.dm_os_windows_info"
        )
        windows = cursor.fetchone()
        cursor.execute(
            "SELECT encrypt_option, auth_scheme, net_transport "
            "FROM sys.dm_exec_connections WHERE session_id=@@SPID"
        )
        connection_info = cursor.fetchone()
        cursor.execute(
            """
            SELECT name, state_desc, compatibility_level, recovery_model_desc,
                   collation_name, page_verify_option_desc, is_auto_close_on,
                   is_auto_shrink_on
            FROM sys.databases
            ORDER BY database_id
            """
        )
        databases = [
            {
                "name": row[0],
                "state": row[1],
                "compatibility_level": row[2],
                "recovery_model": row[3],
                "collation": row[4],
                "page_verify": row[5],
                "auto_close": bool(row[6]),
                "auto_shrink": bool(row[7]),
            }
            for row in cursor.fetchall()
        ]
        result = {
            "server": {
                "product_version": server[0],
                "product_level": server[1],
                "edition": server[2],
                "engine_edition": server[3],
                "collation": server[4],
                "is_clustered": bool(server[5]),
                "full_version": server[6],
            },
            "windows": {
                "release": windows[0],
                "service_pack_level": windows[1],
            },
            "connection": {
                "encrypted": connection_info[0],
                "auth_scheme": connection_info[1],
                "transport": connection_info[2],
            },
            "databases": databases,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
