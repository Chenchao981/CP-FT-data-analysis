from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy.engine import URL


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Check backend readiness")
    parser.add_argument("--server", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", default="TMS_G0_DEV")
    parser.add_argument("--driver", default="ODBC Driver 18 for SQL Server")
    args = parser.parse_args()

    password = getpass.getpass("SQL password: ")
    host, separator, port_text = args.server.partition(",")
    url = URL.create(
        "mssql+pyodbc",
        username=args.user,
        password=password,
        host=host,
        port=int(port_text) if separator else None,
        database=args.database,
        query={
            "driver": args.driver,
            "Encrypt": "no",
            "TrustServerCertificate": "yes",
        },
    )
    os.environ["TMS_DATABASE_URL"] = url.render_as_string(hide_password=False)

    from fastapi.testclient import TestClient

    from app.main import create_app

    response = TestClient(create_app()).get("/api/v1/health/ready")
    response.raise_for_status()
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["schema_revision"] == "sql2014_0006"
    print(payload)
    print("backend_ready=PASS")


if __name__ == "__main__":
    main()
