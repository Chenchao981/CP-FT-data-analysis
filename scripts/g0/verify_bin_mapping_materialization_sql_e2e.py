from __future__ import annotations

"""Rollback-only SQL Server 2014 verification for Bin Mapping materialization."""

import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.infrastructure.database import check_database, get_engine
from app.infrastructure.sql_bin_mapping_materializer import (
    materialize_processing_run_bin_mappings,
)
from app.infrastructure.stage_fact_repository import insert_units

EXPECTED_DATABASE = "TMS_G0_DEV"
EXPECTED_SCHEMA_REVISION = "sql2014_0028"


def _scalar(connection: Connection, sql: str, parameters: dict[str, Any]) -> int:
    return int(connection.execute(text(sql), parameters).scalar_one())


def _create_program_context(
    connection: Connection,
    *,
    supplier_id: int,
    token: str,
    suffix: str,
) -> tuple[int, int]:
    product_id = _scalar(
        connection,
        "INSERT mdm.product(product_code,product_name,active) "
        "OUTPUT INSERTED.product_id VALUES(:code,:name,1)",
        {
            "code": f"BIN-E2E-{token}-{suffix}",
            "name": f"Bin Mapping E2E {suffix}",
        },
    )
    program_id = _scalar(
        connection,
        "INSERT mdm.test_program(supplier_id,product_id,test_stage,program_code,"
        "program_name,active) OUTPUT INSERTED.test_program_id "
        "VALUES(:supplier,:product,'CP',:code,:name,1)",
        {
            "supplier": supplier_id,
            "product": product_id,
            "code": f"BIN-E2E-{suffix}",
            "name": f"Bin Mapping E2E {suffix}",
        },
    )
    program_version_id = _scalar(
        connection,
        "INSERT mdm.test_program_version(test_program_id,version_code,metadata_json) "
        "OUTPUT INSERTED.program_version_id VALUES(:program,:version,'{}')",
        {"program": program_id, "version": f"{token}-{suffix}"},
    )
    return product_id, program_version_id


def _create_run_and_unit(
    connection: Connection,
    *,
    processing_run_id: int,
    supplier_id: int,
    product_id: int,
    program_version_id: int,
    token: str,
    suffix: str,
    raw_bin_code: str,
) -> int:
    run_id = _scalar(
        connection,
        "INSERT test.test_run(processing_run_id,supplier_id,product_id,"
        "program_version_id,test_stage,lot_id,started_at_utc,timezone_resolution,"
        "timestamp_source,metadata_json) OUTPUT INSERTED.run_id "
        "VALUES(:processing,:supplier,:product,:program,'CP',:lot,"
        "SYSUTCDATETIME(),'SOURCE_EXPLICIT','SYSTEM','{}')",
        {
            "processing": processing_run_id,
            "supplier": supplier_id,
            "product": product_id,
            "program": program_version_id,
            "lot": f"BIN-E2E-{token}-{suffix}",
        },
    )
    return insert_units(connection, "CP", [{
        "run_id": run_id, "logical_unit_key": f"CP:BIN-E2E:{token}:{suffix}",
        "attempt_no": 0, "unit_sequence": 1, "soft_bin": raw_bin_code,
        "overall_result": "UNKNOWN", "metadata_json": "{}",
    }])[0]



def _create_mapping_set(
    connection: Connection,
    *,
    supplier_id: int,
    product_id: int,
    program_version_id: int,
    token: str,
    suffix: str,
    raw_bin_code: str,
    is_pass: bool,
    failure_mode: str,
) -> int:
    mapping_set_id = _scalar(
        connection,
        "INSERT mdm.bin_mapping_set(mapping_name,version_code,scope_code,"
        "supplier_id,product_id,test_stage,program_version_id,active) "
        "OUTPUT INSERTED.bin_mapping_set_id VALUES(:name,:version,"
        "'PRODUCT_PROGRAM',:supplier,:product,'CP',:program,1)",
        {
            "name": f"Bin Mapping E2E {suffix}",
            "version": f"{token}-{suffix}",
            "supplier": supplier_id,
            "product": product_id,
            "program": program_version_id,
        },
    )
    connection.execute(
        text(
            "INSERT mdm.bin_definition(bin_mapping_set_id,bin_type,bin_code,"
            "bin_name,failure_mode,is_pass) VALUES(:mapping,'CP_BIN',:bin,"
            ":name,:failure_mode,:is_pass)"
        ),
        {
            "mapping": mapping_set_id,
            "bin": raw_bin_code,
            "name": failure_mode,
            "failure_mode": failure_mode,
            "is_pass": is_pass,
        },
    )
    return mapping_set_id


def verify() -> dict[str, Any]:
    identity = check_database()
    if (
        identity["database"] != EXPECTED_DATABASE
        or identity["schema_revision"] != EXPECTED_SCHEMA_REVISION
    ):
        raise RuntimeError(
            "rollback E2E is restricted to "
            f"{EXPECTED_DATABASE}/{EXPECTED_SCHEMA_REVISION}; got {identity}"
        )

    engine = get_engine()
    token = uuid4().hex[:12].upper()
    supplier_code = f"BIN-E2E-{token}"
    captured_rows: list[dict[str, Any]] = []
    transaction = None
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            base = (
                connection.execute(
                    text(
                        "SELECT TOP (1) pr.job_id,pr.source_file_id,pr.parser_profile_id "
                        "FROM ingestion.processing_run pr "
                        "ORDER BY pr.processing_run_id"
                    )
                )
                .mappings()
                .one_or_none()
            )
            if base is None:
                raise RuntimeError("one existing Processing Run identity is required")

            # Keep the three synthetic contexts isolated from pre-existing broad rules.
            # The transaction is always rolled back, including this temporary deactivation.
            connection.execute(
                text("UPDATE mdm.bin_mapping_set SET active=0 WHERE active=1")
            )

            supplier_id = _scalar(
                connection,
                "INSERT mdm.supplier(supplier_code,supplier_name,supplier_type,active) "
                "OUTPUT INSERTED.supplier_id VALUES(:code,:name,'WAFER_FAB',1)",
                {"code": supplier_code, "name": "Bin Mapping rollback E2E"},
            )
            contexts = {
                suffix: _create_program_context(
                    connection,
                    supplier_id=supplier_id,
                    token=token,
                    suffix=suffix,
                )
                for suffix in ("MATCHED", "AMBIGUOUS", "MISSING")
            }
            processing_run_id = _scalar(
                connection,
                "INSERT ingestion.processing_run(job_id,source_file_id,parser_profile_id,"
                "parser_version,canonical_model_version,status,is_current,row_count_input,"
                "unit_count_output,measurement_count_output,metadata_json) "
                "OUTPUT INSERTED.processing_run_id VALUES(:job,:source,:parser,"
                "'BIN-E2E','1.0','READY',0,3,3,0,:metadata)",
                {
                    "job": int(base["job_id"]),
                    "source": int(base["source_file_id"]),
                    "parser": int(base["parser_profile_id"]),
                    "metadata": json.dumps(
                        {"verification": "bin-mapping-rollback-e2e"}
                    ),
                },
            )
            unit_ids = {
                suffix: _create_run_and_unit(
                    connection,
                    processing_run_id=processing_run_id,
                    supplier_id=supplier_id,
                    product_id=contexts[suffix][0],
                    program_version_id=contexts[suffix][1],
                    token=token,
                    suffix=suffix,
                    raw_bin_code={
                        "MATCHED": "1",
                        "AMBIGUOUS": "7",
                        "MISSING": "9",
                    }[suffix],
                )
                for suffix in contexts
            }

            matched_mapping_set_id = _create_mapping_set(
                connection,
                supplier_id=supplier_id,
                product_id=contexts["MATCHED"][0],
                program_version_id=contexts["MATCHED"][1],
                token=token,
                suffix="MATCHED",
                raw_bin_code="1",
                is_pass=True,
                failure_mode="PASS_BIN",
            )
            for suffix in ("AMBIGUOUS-A", "AMBIGUOUS-B"):
                _create_mapping_set(
                    connection,
                    supplier_id=supplier_id,
                    product_id=contexts["AMBIGUOUS"][0],
                    program_version_id=contexts["AMBIGUOUS"][1],
                    token=token,
                    suffix=suffix,
                    raw_bin_code="7",
                    is_pass=False,
                    failure_mode=suffix,
                )

            materialize_processing_run_bin_mappings(
                connection,
                processing_run_id=processing_run_id,
            )
            materialize_processing_run_bin_mappings(
                connection,
                processing_run_id=processing_run_id,
            )

            rows = (
                connection.execute(
                    text(
                        "SELECT ur.logical_unit_key,ube.bin_type,ube.raw_bin_code,"
                        "ube.bin_mapping_set_id,ube.bin_definition_id,ube.mapping_status,"
                        "ube.is_pass_snapshot,ube.failure_mode_snapshot "
                        "FROM test.unit_bin_evaluation ube "
                        "JOIN test.unit_result ur ON ur.unit_id=ube.unit_id "
                        "WHERE ube.processing_run_id=:processing "
                        "ORDER BY ur.logical_unit_key"
                    ),
                    {"processing": processing_run_id},
                )
                .mappings()
                .all()
            )
            captured_rows = [dict(row) for row in rows]
            if len(captured_rows) != 3:
                raise AssertionError(f"idempotence failed: {captured_rows}")
            by_suffix = {
                str(row["logical_unit_key"]).rsplit(":", 1)[-1]: row
                for row in captured_rows
            }
            matched = by_suffix["MATCHED"]
            if not (
                matched["mapping_status"] == "MATCHED"
                and int(matched["bin_mapping_set_id"]) == matched_mapping_set_id
                and matched["bin_definition_id"] is not None
                and bool(matched["is_pass_snapshot"])
                and matched["failure_mode_snapshot"] == "PASS_BIN"
            ):
                raise AssertionError(f"positive mapping failed: {matched}")
            for suffix, status in (
                ("AMBIGUOUS", "CONFIG_AMBIGUOUS"),
                ("MISSING", "NO_MATCH"),
            ):
                row = by_suffix[suffix]
                if row["mapping_status"] != status:
                    raise AssertionError(f"{suffix} status failed: {row}")
                if any(
                    row[field] is not None
                    for field in (
                        "bin_mapping_set_id",
                        "bin_definition_id",
                        "is_pass_snapshot",
                        "failure_mode_snapshot",
                    )
                ):
                    raise AssertionError(f"{suffix} leaked Mapping semantics: {row}")
            if set(unit_ids.values()) != {
                int(
                    connection.execute(
                        text(
                            "SELECT unit_id FROM test.unit_result "
                            "WHERE logical_unit_key=:key"
                        ),
                        {"key": row["logical_unit_key"]},
                    ).scalar_one()
                )
                for row in captured_rows
            }:
                raise AssertionError("fixture unit reconciliation failed")
        finally:
            if transaction is not None and transaction.is_active:
                transaction.rollback()

    with engine.connect() as connection:
        remaining = int(
            connection.execute(
                text("SELECT COUNT_BIG(*) FROM mdm.supplier WHERE supplier_code=:code"),
                {"code": supplier_code},
            ).scalar_one()
        )
    if remaining != 0:
        raise AssertionError("rollback verification fixture escaped its transaction")

    return {
        "database": identity["database"],
        "database_version": identity["database_version"],
        "schema_revision": identity["schema_revision"],
        "statuses": [row["mapping_status"] for row in captured_rows],
        "evaluation_count_after_second_materialization": len(captured_rows),
        "rollback_clean": True,
    }


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
