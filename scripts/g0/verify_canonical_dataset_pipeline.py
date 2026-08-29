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

from app.cleaners.huahong_dcp import HuaHongDcpParser  # noqa: E402
from app.domain.auth import Principal  # noqa: E402
from app.domain.datasets import (  # noqa: E402
    CreateDatasetRequest,
    CreateDatasetVersionRequest,
    PublishDatasetVersionRequest,
)
from app.domain.jobs import (  # noqa: E402
    CreateJobRequest,
    JobStatus,
    TransitionJobRequest,
)
from app.infrastructure.canonical_writer import (  # noqa: E402
    HuaHongCanonicalWriter,
    HuaHongWriteContext,
    SourceFileRepository,
    SourceRegistration,
)
from app.infrastructure.sql_dataset_service import SqlDatasetService  # noqa: E402
from app.infrastructure.sql_job_service import SqlJobService  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify canonical write, DQ gate, dataset publish, and summary"
    )
    parser.add_argument("--server", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", default="TMS_G0_DEV")
    parser.add_argument("--driver", default="ODBC Driver 18 for SQL Server")
    return parser.parse_args()


def fixture_text(lot: str) -> str:
    parameters = "CONT\tIGSS0\tIGSS1\tIGSSR1\tVTH\tBVDSS1\tBVDSS2\tIDSS1\tIDSS2\tIGSS2\tIGSSR2"
    upper = "0.500V\t99.00uA\t100.0nA\t100.0nA\t3.900V\t140.0V\t140.0V\t100.0nA\t200.0nA\t200.0nA\t200.0nA"
    lower = "0V\t0A\t0A\t0A\t2.400V\t120.0V\t120.0V\t0A\t0A\t0A\t0A"
    values = "1E-3\t2E-8\t3E-8\t4E-8\t3.1\t130\t131\t1E-8\t2E-8\t3E-8\t4E-8"
    return "\n".join(
        [
            "Program name\tG0_VERIFY.jtf",
            f"Lot number\t{lot}",
            "Wafer number\t1",
            "Date\t2026/08/21",
            "Time\t09:00:00",
            "",
            f"No.U\tX\tY\tBin\t{parameters}",
            f"LimitU\t\t\t\t{upper}",
            f"LimitL\t\t\t\t{lower}",
            *[f"Bias {number}" + "\t" * 14 for number in range(1, 7)],
            f"1\t1\t1\t1\t{values}",
        ]
    )


def main() -> None:
    args = parse_args()
    password = os.getenv("TMS_SQL_PASSWORD") or getpass.getpass("SQL password: ")
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
    engine = create_engine(url, pool_pre_ping=True)
    token = uuid4().hex.upper()
    business_lot = f"{token[:4]}-{token[4:8]}"
    source_lot = f"{business_lot}-000A-260821@203"
    source_name = f"{source_lot}_001.TXT"
    content = fixture_text(source_lot)
    content_bytes = content.encode("utf-8")
    parsed = HuaHongDcpParser().parse_bytes(content_bytes, source_name=source_name)

    ids: dict[str, int] = {}
    parser_created = False
    supplier_created = False
    try:
        with engine.begin() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if revision != "sql2014_0006":
                raise RuntimeError(f"unexpected schema revision: {revision}")
            ids["user"] = int(
                connection.execute(
                    text(
                        "INSERT iam.app_user(login_name,display_name,identity_provider,external_subject,status) "
                        "OUTPUT INSERTED.user_id VALUES(:login,:display,'AD',:subject,'ACTIVE')"
                    ),
                    {"login": f"g0_{token}", "display": "G0 integration", "subject": token},
                ).scalar_one()
            )
            ids["supplier"] = int(
                connection.execute(
                    text(
                        "INSERT mdm.supplier(supplier_code,supplier_name,supplier_type,active) "
                        "OUTPUT INSERTED.supplier_id VALUES(:code,'G0 HuaHong','WAFER_FAB',1)"
                    ),
                    {"code": f"G0_{token}"},
                ).scalar_one()
            )
            supplier_created = True
            ids["program"] = int(
                connection.execute(
                    text(
                        "INSERT mdm.test_program(supplier_id,product_id,test_stage,program_code,active) "
                        "OUTPUT INSERTED.test_program_id VALUES(:supplier,:product,'CP',:code,1)"
                    ),
                    {
                        "supplier": ids["supplier"],
                        "product": None,
                        "code": f"G0_{token}",
                    },
                ).scalar_one()
            )
            ids["program_version"] = int(
                connection.execute(
                    text(
                        "INSERT mdm.test_program_version(test_program_id,version_code,raw_program_name) "
                        "OUTPUT INSERTED.program_version_id VALUES(:program,'1.0',:raw_name)"
                    ),
                    {"program": ids["program"], "raw_name": parsed.program_name},
                ).scalar_one()
            )
            item_ids: dict[str, int] = {}
            for index, spec in enumerate(parsed.specs, start=1):
                item_ids[spec.name] = int(
                    connection.execute(
                        text(
                            "INSERT mdm.test_item_definition("
                            "program_version_id,sequence_no,step_code,raw_item_name,data_type,"
                            "unit_raw,unit_code,program_lsl,program_usl,lower_limit_raw,upper_limit_raw,"
                            "condition_json,source_column_index) OUTPUT INSERTED.test_item_id VALUES("
                            ":program_version,:sequence,:step,:name,'NUMERIC',:unit_raw,:unit_code,"
                            ":lsl,:usl,:lower_raw,:upper_raw,:conditions,:column_index)"
                        ),
                        {
                            "program_version": ids["program_version"],
                            "sequence": index,
                            "step": f"P{index:03d}",
                            "name": spec.name,
                            "unit_raw": spec.upper.raw or spec.lower.raw,
                            "unit_code": spec.upper.unit_base or spec.lower.unit_base,
                            "lsl": spec.lower.value_base,
                            "usl": spec.upper.value_base,
                            "lower_raw": spec.lower.raw,
                            "upper_raw": spec.upper.raw,
                            "conditions": "{}",
                            "column_index": index + 3,
                        },
                    ).scalar_one()
                )
            ids["format_profile"] = int(
                connection.execute(
                    text(
                        "INSERT ingestion.format_profile("
                        "supplier_id,test_stage,format_code,profile_version,signature_json,"
                        "file_role_contract_json,status,approved_by,approved_at_utc) "
                        "OUTPUT INSERTED.format_profile_id VALUES("
                        ":supplier,'CP','HUAHONG_DCP_TXT',:version,'{}','{}','RELEASED',"
                        ":user,SYSUTCDATETIME())"
                    ),
                    {
                        "supplier": ids["supplier"],
                        "version": f"g0-{token}",
                        "user": ids["user"],
                    },
                ).scalar_one()
            )
            ids["cleaner_release"] = int(
                connection.execute(
                    text(
                        "INSERT ingestion.cleaner_release("
                        "format_profile_id,cleaner_code,cleaner_version,code_checksum,status,"
                        "approved_by,approved_at_utc) OUTPUT INSERTED.cleaner_release_id VALUES("
                        ":profile,:code,'1.0',:checksum,'RELEASED',:user,SYSUTCDATETIME())"
                    ),
                    {
                        "profile": ids["format_profile"],
                        "code": f"G0_{token}",
                        "checksum": token.lower().ljust(64, "0"),
                        "user": ids["user"],
                    },
                ).scalar_one()
            )
            parser_row = connection.execute(
                text(
                    "SELECT parser_profile_id,supplier_id,test_stage,active FROM ingestion.parser_profile "
                    "WHERE format_code='HUAHONG_DCP_TXT' AND parser_version='1.0'"
                )
            ).mappings().one_or_none()
            if parser_row is None:
                ids["parser_profile"] = int(
                    connection.execute(
                        text(
                            "INSERT ingestion.parser_profile("
                            "format_code,test_stage,parser_name,parser_version,canonical_model_version,"
                            "active,is_default) OUTPUT INSERTED.parser_profile_id VALUES("
                            "'HUAHONG_DCP_TXT','CP','HuaHongDcpParser','1.0','1.0',1,0)"
                        )
                    ).scalar_one()
                )
                parser_created = True
            else:
                if parser_row["test_stage"] != "CP" or not parser_row["active"]:
                    raise RuntimeError("existing HuaHong parser profile 1.0 is not active for CP")
                ids["parser_profile"] = int(parser_row["parser_profile_id"])
                if parser_row["supplier_id"] is not None:
                    approved_supplier_id = int(parser_row["supplier_id"])
                    connection.execute(
                        text("UPDATE mdm.test_program SET supplier_id=:supplier WHERE test_program_id=:program"),
                        {"supplier": approved_supplier_id, "program": ids["program"]},
                    )
                    connection.execute(
                        text("UPDATE ingestion.format_profile SET supplier_id=:supplier WHERE format_profile_id=:profile"),
                        {"supplier": approved_supplier_id, "profile": ids["format_profile"]},
                    )
                    connection.execute(
                        text("DELETE mdm.supplier WHERE supplier_id=:supplier"),
                        {"supplier": ids["supplier"]},
                    )
                    ids["supplier"] = approved_supplier_id
                    supplier_created = False
            ids["batch"] = int(
                connection.execute(
                    text(
                        "INSERT ingestion.import_batch(source_channel,status,metadata_json) "
                        "OUTPUT INSERTED.import_batch_id VALUES('SYSTEM','RECEIVED',:metadata)"
                    ),
                    {"metadata": '{"purpose":"g0-integration"}'},
                ).scalar_one()
            )

        receipt = SourceFileRepository(engine).register(
            SourceRegistration(
                sha256=parsed.source_sha256,
                file_size=len(content_bytes),
                canonical_storage_uri=f"test://g0/{token}/{source_name}",
                original_file_name=source_name,
                import_batch_id=ids["batch"],
                received_by=f"g0_{token}",
                received_channel="SYSTEM",
            )
        )
        ids["source_file"] = receipt.source_file_id
        ids["receipt"] = receipt.receipt_id
        job_service = SqlJobService(engine)
        job = job_service.create(
            CreateJobRequest(
                source_file_id=ids["source_file"],
                cleaner_release_id=ids["cleaner_release"],
                job_type="PARSE",
                trigger_type="SYSTEM",
                requested_by=f"g0_{token}",
                reason="canonical and dataset integration verification",
            )
        )
        ids["job"] = job.job_id
        job_service.transition(job.job_id, TransitionJobRequest(target_status=JobStatus.RUNNING))
        write_result = HuaHongCanonicalWriter(engine).write(
            parsed,
            HuaHongWriteContext(
                source_file_id=ids["source_file"],
                job_id=ids["job"],
                parser_profile_id=ids["parser_profile"],
                supplier_id=ids["supplier"],
                product_id=None,
                program_version_id=ids["program_version"],
                test_item_ids=item_ids,
            ),
        )
        ids["processing_run"] = write_result.processing_run_id
        ids["test_run"] = write_result.test_run_id

        dataset_service = SqlDatasetService(engine)
        dataset = dataset_service.create_dataset(
            CreateDatasetRequest(
                dataset_code=f"G0_{token}",
                dataset_name="G0 canonical publication verification",
                dataset_type="CP_DETAIL",
                test_stage="CP",
                supplier_id=ids["supplier"],
                product_id=None,
                owner_user_id=ids["user"],
            )
        )
        ids["dataset"] = dataset.dataset_id
        version = dataset_service.create_version(
            dataset.dataset_id,
            CreateDatasetVersionRequest(
                input_batch_id=ids["batch"],
                processing_run_ids=[ids["processing_run"]],
            ),
        )
        ids["dataset_version"] = version.dataset_version_id
        principal = Principal(
            user_id=ids["user"],
            login_name=f"g0_{token}",
            display_name="G0 integration",
            roles=("DATA_ENGINEER",),
            permissions=frozenset({"DATASET_READ"}),
        )
        gate = dataset_service.evaluate_gate(
            dataset.dataset_id, version.version_no, principal
        )
        if gate.status != "PASS":
            raise RuntimeError(f"unexpected DQ gate result: {gate}")
        published = dataset_service.publish(
            dataset.dataset_id,
            version.version_no,
            PublishDatasetVersionRequest(published_by=ids["user"]),
        )
        summary = dataset_service.get_summary(
            dataset.dataset_id, version.version_no, principal
        )
        charts = dataset_service.get_chart_data(
            dataset.dataset_id,
            version.version_no,
            lot_id=parsed.business_lot_id,
            wafer_id=parsed.wafer_number,
        )
        if not (
            published.status == "PUBLISHED"
            and summary.unit_count == 1
            and summary.pass_count == 1
            and summary.measurement_count == len(parsed.parameters)
            and summary.bin_counts == {"1": 1}
            and len(charts.wafer_yield) == 1
            and charts.wafer_yield[0].yield_rate == 1.0
            and len(charts.bin_counts) == 1
            and charts.bin_counts[0].soft_bin == "1"
            and len(charts.wafer_map) == 1
            and charts.wafer_map[0].x == 1
            and charts.wafer_map[0].y == 1
        ):
            raise RuntimeError(
                f"unexpected published summary/charts: {summary} / {charts}"
            )
        job_service.transition(job.job_id, TransitionJobRequest(target_status=JobStatus.SUCCESS))
        print(
            "canonical_dataset_pipeline=PASS "
            f"units={summary.unit_count} measurements={summary.measurement_count} "
            f"yield={summary.yield_rate:.6f} map_points={len(charts.wafer_map)}"
        )
    finally:
        with engine.begin() as connection:
            if "dataset_version" in ids:
                connection.execute(text("DELETE dataset.dataset_version_run WHERE dataset_version_id=:id"), {"id": ids["dataset_version"]})
                connection.execute(text("DELETE dataset.dataset_version WHERE dataset_version_id=:id"), {"id": ids["dataset_version"]})
            if "dataset" in ids:
                connection.execute(text("DELETE dataset.dataset WHERE dataset_id=:id"), {"id": ids["dataset"]})
            if "processing_run" in ids:
                connection.execute(text("DELETE test.measurement WHERE unit_id IN (SELECT unit_id FROM test.unit_result WHERE run_id=:run_id)"), {"run_id": ids.get("test_run")})
                connection.execute(text("DELETE test.unit_result WHERE run_id=:run_id"), {"run_id": ids.get("test_run")})
                connection.execute(text("DELETE test.test_run WHERE processing_run_id=:id"), {"id": ids["processing_run"]})
                connection.execute(text("DELETE ingestion.processing_run WHERE processing_run_id=:id"), {"id": ids["processing_run"]})
            if "job" in ids:
                connection.execute(text("DELETE ingestion.processing_job WHERE job_id=:id"), {"id": ids["job"]})
            if "receipt" in ids:
                connection.execute(text("DELETE ingestion.source_file_receipt WHERE receipt_id=:id"), {"id": ids["receipt"]})
            if "source_file" in ids:
                connection.execute(text("DELETE ingestion.source_file WHERE source_file_id=:id"), {"id": ids["source_file"]})
            if "cleaner_release" in ids:
                connection.execute(text("DELETE ingestion.cleaner_release WHERE cleaner_release_id=:id"), {"id": ids["cleaner_release"]})
            if "format_profile" in ids:
                connection.execute(text("DELETE ingestion.format_profile WHERE format_profile_id=:id"), {"id": ids["format_profile"]})
            if "program_version" in ids:
                connection.execute(text("DELETE mdm.test_item_definition WHERE program_version_id=:id"), {"id": ids["program_version"]})
                connection.execute(text("DELETE mdm.test_program_version WHERE program_version_id=:id"), {"id": ids["program_version"]})
            if "program" in ids:
                connection.execute(text("DELETE mdm.test_program WHERE test_program_id=:id"), {"id": ids["program"]})
            if parser_created and "parser_profile" in ids:
                connection.execute(text("DELETE ingestion.parser_profile WHERE parser_profile_id=:id"), {"id": ids["parser_profile"]})
            if "product" in ids:
                connection.execute(text("DELETE mdm.product WHERE product_id=:id"), {"id": ids["product"]})
            if supplier_created and "supplier" in ids:
                connection.execute(text("DELETE mdm.supplier WHERE supplier_id=:id"), {"id": ids["supplier"]})
            if "batch" in ids:
                connection.execute(text("DELETE ingestion.import_batch WHERE import_batch_id=:id"), {"id": ids["batch"]})
            if "user" in ids:
                connection.execute(text("DELETE iam.app_user WHERE user_id=:id"), {"id": ids["user"]})
        audit_targets = [
            ("iam.app_user", "user_id", "user"),
            ("mdm.product", "product_id", "product"),
            ("mdm.test_program", "test_program_id", "program"),
            ("mdm.test_program_version", "program_version_id", "program_version"),
            ("mdm.test_item_definition", "program_version_id", "program_version"),
            ("ingestion.format_profile", "format_profile_id", "format_profile"),
            ("ingestion.cleaner_release", "cleaner_release_id", "cleaner_release"),
            ("ingestion.import_batch", "import_batch_id", "batch"),
            ("ingestion.source_file", "source_file_id", "source_file"),
            ("ingestion.source_file_receipt", "receipt_id", "receipt"),
            ("ingestion.processing_job", "job_id", "job"),
            ("ingestion.processing_run", "processing_run_id", "processing_run"),
            ("test.test_run", "run_id", "test_run"),
            ("test.unit_result", "run_id", "test_run"),
            ("dataset.dataset", "dataset_id", "dataset"),
            ("dataset.dataset_version", "dataset_version_id", "dataset_version"),
            ("dataset.dataset_version_run", "dataset_version_id", "dataset_version"),
        ]
        if parser_created:
            audit_targets.append(
                ("ingestion.parser_profile", "parser_profile_id", "parser_profile")
            )
        if supplier_created:
            audit_targets.append(("mdm.supplier", "supplier_id", "supplier"))
        leftovers: dict[str, int] = {}
        with engine.connect() as connection:
            for table, column, key in audit_targets:
                if key not in ids:
                    continue
                count = int(
                    connection.execute(
                        text(f"SELECT COUNT(*) FROM {table} WHERE {column}=:id"),
                        {"id": ids[key]},
                    ).scalar_one()
                )
                if count:
                    leftovers[table] = count
        if leftovers:
            raise RuntimeError(f"integration cleanup left database rows: {leftovers}")
        print("integration_cleanup=PASS")
        engine.dispose()


if __name__ == "__main__":
    main()
