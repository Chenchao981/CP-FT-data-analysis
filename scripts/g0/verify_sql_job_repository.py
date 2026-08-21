from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the Processing Job API against SQL Server"
    )
    parser.add_argument("--server", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", default="TMS_G0_DEV")
    parser.add_argument("--driver", default="ODBC Driver 18 for SQL Server")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
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
    os.environ["TMS_JOB_REPOSITORY"] = "sql"

    token = uuid4().hex
    engine = create_engine(url)
    source_file_id: int | None = None
    format_profile_id: int | None = None
    cleaner_release_id: int | None = None
    job_id: int | None = None
    try:
        with engine.begin() as connection:
            source_file_id = connection.execute(
                text(
                    "INSERT ingestion.source_file(sha256, file_size, canonical_storage_uri) "
                    "OUTPUT INSERTED.source_file_id VALUES(:sha256, 0, :uri)"
                ),
                {"sha256": token.ljust(64, "0"), "uri": f"test://g0/{token}"},
            ).scalar_one()
            format_profile_id = connection.execute(
                text(
                    "INSERT ingestion.format_profile(test_stage, format_code, profile_version, "
                    "signature_json, file_role_contract_json, status) "
                    "OUTPUT INSERTED.format_profile_id "
                    "VALUES('CP', :code, 'integration', '{}', '{}', 'RELEASED')"
                ),
                {"code": f"G0_{token}"},
            ).scalar_one()
            cleaner_release_id = connection.execute(
                text(
                    "INSERT ingestion.cleaner_release(format_profile_id, cleaner_code, "
                    "cleaner_version, code_checksum, status) "
                    "OUTPUT INSERTED.cleaner_release_id "
                    "VALUES(:profile_id, :code, 'integration', :checksum, 'RELEASED')"
                ),
                {
                    "profile_id": format_profile_id,
                    "code": f"G0_{token}",
                    "checksum": token.ljust(64, "0"),
                },
            ).scalar_one()

        from fastapi.testclient import TestClient

        from app.main import create_app

        client = TestClient(create_app())
        created = client.post(
            "/api/v1/jobs",
            json={
                "source_file_id": source_file_id,
                "cleaner_release_id": cleaner_release_id,
                "job_type": "PARSE",
                "trigger_type": "MANUAL",
                "requested_by": "g0-integration-test",
                "reason": "SQL repository verification",
            },
        )
        created.raise_for_status()
        job_id = created.json()["job_id"]
        assert created.json()["status"] == "QUEUED"

        running = client.post(
            f"/api/v1/jobs/{job_id}/transitions",
            json={"target_status": "RUNNING"},
        )
        running.raise_for_status()
        assert running.json()["status"] == "RUNNING"

        completed = client.post(
            f"/api/v1/jobs/{job_id}/transitions",
            json={"target_status": "SUCCESS"},
        )
        completed.raise_for_status()
        assert completed.json()["status"] == "SUCCESS"
        assert completed.json()["started_at_utc"] is not None
        assert completed.json()["finished_at_utc"] is not None
        print("sql_job_repository=PASS")
    finally:
        with engine.begin() as connection:
            if job_id is not None:
                connection.execute(
                    text("DELETE ingestion.processing_job WHERE job_id=:job_id"),
                    {"job_id": job_id},
                )
            if cleaner_release_id is not None:
                connection.execute(
                    text(
                        "DELETE ingestion.cleaner_release "
                        "WHERE cleaner_release_id=:cleaner_release_id"
                    ),
                    {"cleaner_release_id": cleaner_release_id},
                )
            if format_profile_id is not None:
                connection.execute(
                    text(
                        "DELETE ingestion.format_profile "
                        "WHERE format_profile_id=:format_profile_id"
                    ),
                    {"format_profile_id": format_profile_id},
                )
            if source_file_id is not None:
                connection.execute(
                    text(
                        "DELETE ingestion.source_file WHERE source_file_id=:source_file_id"
                    ),
                    {"source_file_id": source_file_id},
                )
        engine.dispose()


if __name__ == "__main__":
    main()
