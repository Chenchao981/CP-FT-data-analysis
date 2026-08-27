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

from app.domain.auth import Principal
from app.domain.enrichments import CreateFieldEnrichmentRequest
from app.infrastructure.sql_enrichment_service import (
    SqlFieldEnrichmentService,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify manual CP/FT field enrichment")
    parser.add_argument("--server")
    parser.add_argument("--user")
    parser.add_argument("--database", default="TMS_G0_DEV")
    parser.add_argument("--driver", default="ODBC Driver 18 for SQL Server")
    args = parser.parse_args()
    database_url = os.environ.get("TMS_DATABASE_URL")
    if database_url:
        engine = create_engine(database_url)
    else:
        if not args.server or not args.user:
            parser.error("set TMS_DATABASE_URL or provide both --server and --user")
        password = getpass.getpass("SQL password: ")
        host, separator, port_text = args.server.partition(",")
        engine = create_engine(
            URL.create(
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
        )
    token = uuid4().hex[:12]
    ids: dict[str, int] = {}
    try:
        with engine.begin() as connection:
            ids["user"] = int(
                connection.execute(
                    text(
                        "INSERT iam.app_user(login_name,display_name,identity_provider,external_subject,status) "
                        "OUTPUT INSERTED.user_id VALUES(:login,'G0 enrichment','AD',:subject,'ACTIVE')"
                    ),
                    {"login": f"enrich_{token}", "subject": token},
                ).scalar_one()
            )
            ids["batch"] = int(
                connection.execute(
                    text(
                        "INSERT ingestion.import_batch(source_channel,uploaded_by,status,"
                        "owner_user_id,business_domain,test_stage) "
                        "OUTPUT INSERTED.import_batch_id "
                        "VALUES('MANUAL',:login,'RECEIVED',:user_id,'ENGINEERING','CP')"
                    ),
                    {"login": f"enrich_{token}", "user_id": ids["user"]},
                ).scalar_one()
            )
        service = SqlFieldEnrichmentService(engine)
        principal = Principal(
            user_id=ids["user"],
            login_name=f"enrich_{token}",
            display_name="G0 enrichment",
            roles=(),
            permissions=frozenset(),
        )
        first = service.create(
            CreateFieldEnrichmentRequest(
                import_batch_id=ids["batch"],
                test_stage="CP",
                field_code="SUPPLIER_CODE",
                action="FILL",
                value_text="HUAHONG",
                entered_by=ids["user"],
                reason="G0 CP manual source verification",
            ),
            principal,
        )
        replacement = service.create(
            CreateFieldEnrichmentRequest(
                import_batch_id=ids["batch"],
                test_stage="CP",
                field_code="SUPPLIER_CODE",
                action="FILL",
                value_text="HUAHONG_APPROVED",
                entered_by=ids["user"],
                reason="G0 version replacement verification",
            ),
            principal,
        )
        ignored = service.create(
            CreateFieldEnrichmentRequest(
                import_batch_id=ids["batch"],
                test_stage="CP",
                field_code="PROJECT_CODE",
                action="IGNORE",
                entered_by=ids["user"],
                reason="G0 optional project code intentionally omitted",
            ),
            principal,
        )
        current = service.list_current(ids["batch"], principal)
        if not (
            first.enrichment_id != replacement.enrichment_id
            and len(current) == 2
            and {item.test_stage for item in current} == {"CP"}
            and ignored.value_text is None
        ):
            raise RuntimeError(f"unexpected enrichment result: {current}")
        print("manual_field_enrichment=PASS current_records=2 version_replacement=PASS")
    finally:
        with engine.begin() as connection:
            if "batch" in ids:
                connection.execute(
                    text("DELETE ingestion.field_enrichment WHERE import_batch_id=:id"),
                    {"id": ids["batch"]},
                )
                connection.execute(
                    text("DELETE ingestion.import_batch WHERE import_batch_id=:id"),
                    {"id": ids["batch"]},
                )
            if "user" in ids:
                connection.execute(
                    text("DELETE iam.app_user WHERE user_id=:id"),
                    {"id": ids["user"]},
                )
        print("enrichment_cleanup=PASS")


if __name__ == "__main__":
    main()
