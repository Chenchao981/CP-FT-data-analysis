from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.enrichments import CreateFieldEnrichmentRequest  # noqa: E402
from app.infrastructure.sql_enrichment_service import SqlFieldEnrichmentService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify manual CP/FT field enrichment")
    parser.add_argument("--server", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", default="TMS_G0_DEV")
    parser.add_argument("--driver", default="ODBC Driver 18 for SQL Server")
    args = parser.parse_args()
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
            query={"driver": args.driver, "Encrypt": "no", "TrustServerCertificate": "yes"},
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
                        "INSERT ingestion.import_batch(source_channel,uploaded_by,status) "
                        "OUTPUT INSERTED.import_batch_id VALUES('MANUAL',:user,'RECEIVED')"
                    ),
                    {"user": f"enrich_{token}"},
                ).scalar_one()
            )
        service = SqlFieldEnrichmentService(engine)
        first = service.create(
            CreateFieldEnrichmentRequest(
                import_batch_id=ids["batch"],
                test_stage="CP",
                field_code="SUPPLIER_CODE",
                action="FILL",
                value_text="HUAHONG",
                entered_by=ids["user"],
                reason="G0 CP manual source verification",
            )
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
            )
        )
        ignored = service.create(
            CreateFieldEnrichmentRequest(
                import_batch_id=ids["batch"],
                test_stage="FT",
                field_code="LOT_ID",
                action="IGNORE",
                entered_by=ids["user"],
                reason="G0 FT file has no Lot and analysis does not require it",
            )
        )
        current = service.list_current(ids["batch"])
        if not (
            first.enrichment_id != replacement.enrichment_id
            and len(current) == 2
            and {item.test_stage for item in current} == {"CP", "FT"}
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
