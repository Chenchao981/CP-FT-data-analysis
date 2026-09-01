from __future__ import annotations

"""Rollback-only SQL Server 2014 verification for formal Spec materialization."""

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
from app.infrastructure.sql_spec_evaluation_materializer import (
    materialize_processing_run_spec_evaluations,
)

EXPECTED_DATABASE = "TMS_G0_DEV"
EXPECTED_SCHEMA_REVISION = "sql2014_0024"


def _scalar(connection: Connection, sql: str, parameters: dict[str, Any]) -> int:
    return int(connection.execute(text(sql), parameters).scalar_one())


def _create_context(
    connection: Connection,
    *,
    base: dict[str, Any],
    token: str,
) -> tuple[int, int, int, int, dict[str, int], int]:
    supplier_id = _scalar(
        connection,
        "INSERT mdm.supplier(supplier_code,supplier_name,supplier_type,active) "
        "OUTPUT INSERTED.supplier_id VALUES(:code,:name,'WAFER_FAB',1)",
        {
            "code": f"SPEC-E2E-{token}",
            "name": "Formal Spec rollback E2E",
        },
    )
    product_id = _scalar(
        connection,
        "INSERT mdm.product(product_code,product_name,active) "
        "OUTPUT INSERTED.product_id VALUES(:code,:name,1)",
        {
            "code": f"SPEC-E2E-{token}",
            "name": "Formal Spec rollback E2E",
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
            "code": f"SPEC-E2E-{token}",
            "name": "Formal Spec rollback E2E",
        },
    )
    program_version_id = _scalar(
        connection,
        "INSERT mdm.test_program_version(test_program_id,version_code,metadata_json) "
        "OUTPUT INSERTED.program_version_id VALUES(:program,:version,'{}')",
        {"program": program_id, "version": token},
    )
    processing_run_id = _scalar(
        connection,
        "INSERT ingestion.processing_run(job_id,source_file_id,parser_profile_id,"
        "parser_version,canonical_model_version,status,is_current,row_count_input,"
        "unit_count_output,measurement_count_output,metadata_json) "
        "OUTPUT INSERTED.processing_run_id VALUES(:job,:source,:parser,"
        "'SPEC-E2E','1.0','READY',0,1,1,6,:metadata)",
        {
            "job": int(base["job_id"]),
            "source": int(base["source_file_id"]),
            "parser": int(base["parser_profile_id"]),
            "metadata": json.dumps(
                {"verification": "formal-spec-materialization-rollback-e2e"}
            ),
        },
    )
    condition_json = json.dumps({"condition": "SPEC-E2E"})
    step_codes = (
        "PASS",
        "FAIL",
        "NO_MATCH",
        "CONFIG_AMBIGUOUS",
        "NOT_EVALUATED",
        "INVALID_VALUE",
    )
    connection.execute(
        text(
            "INSERT mdm.test_item_definition(program_version_id,sequence_no,"
            "step_code,raw_item_name,canonical_parameter_code,display_name,data_type,"
            "unit_raw,unit_code,condition_json,source_column_index,is_analysis_parameter) "
            "VALUES(:program,:sequence,:step,:step,:step,:step,'NUMERIC','V','V',"
            ":condition,:sequence,1)"
        ),
        [
            {
                "program": program_version_id,
                "sequence": index,
                "step": step_code,
                "condition": condition_json,
            }
            for index, step_code in enumerate(step_codes, start=1)
        ],
    )
    item_rows = (
        connection.execute(
            text(
                "SELECT step_code,test_item_id FROM mdm.test_item_definition "
                "WHERE program_version_id=:program"
            ),
            {"program": program_version_id},
        )
        .mappings()
        .all()
    )
    item_ids = {str(row["step_code"]): int(row["test_item_id"]) for row in item_rows}
    spec_set_id = _scalar(
        connection,
        "INSERT mdm.spec_set(product_id,test_stage,spec_name,version_code,status,"
        "source_type,source_ref,metadata_json) OUTPUT INSERTED.spec_set_id "
        "VALUES(:product,'CP',:name,:version,'RELEASED','VERIFICATION',:source,'{}')",
        {
            "product": product_id,
            "name": "Formal Spec rollback E2E",
            "version": token,
            "source": f"rollback-e2e:{token}",
        },
    )
    binding_id = _scalar(
        connection,
        "INSERT mdm.spec_binding(spec_set_id,scope_code,supplier_id,product_id,"
        "test_stage,program_version_id,active) OUTPUT INSERTED.spec_binding_id "
        "VALUES(:spec,'PRODUCT_PROGRAM',:supplier,:product,'CP',:program,1)",
        {
            "spec": spec_set_id,
            "supplier": supplier_id,
            "product": product_id,
            "program": program_version_id,
        },
    )
    for step_code in (
        "PASS",
        "FAIL",
        "NOT_EVALUATED",
        "INVALID_VALUE",
    ):
        connection.execute(
            text(
                "INSERT mdm.spec_item(spec_set_id,test_item_id,"
                "canonical_parameter_code,lsl,usl,lower_operator,upper_operator,"
                "unit_code,condition_json) VALUES(:spec,:item,:step,0,10,'>=','<=',"
                "'V',:condition)"
            ),
            {
                "spec": spec_set_id,
                "item": item_ids[step_code],
                "step": step_code,
                "condition": condition_json,
            },
        )
    for suffix in ("A", "B"):
        connection.execute(
            text(
                "INSERT mdm.spec_item(spec_set_id,test_item_id,"
                "canonical_parameter_code,lsl,usl,lower_operator,upper_operator,"
                "unit_code,condition_json,raw_spec) VALUES(:spec,:item,:step,0,10,"
                "'>=','<=','V',:condition,:raw_spec)"
            ),
            {
                "spec": spec_set_id,
                "item": item_ids["CONFIG_AMBIGUOUS"],
                "step": "CONFIG_AMBIGUOUS",
                "condition": condition_json,
                "raw_spec": suffix,
            },
        )
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
            "lot": f"SPEC-E2E-{token}",
        },
    )
    unit_id = _scalar(
        connection,
        "INSERT test.unit_result(run_id,logical_unit_key,attempt_no,unit_sequence,"
        "soft_bin,overall_result,metadata_json) OUTPUT INSERTED.unit_id "
        "VALUES(:run,:key,0,1,'1','UNKNOWN','{}')",
        {"run": run_id, "key": f"CP:SPEC-E2E:{token}"},
    )
    measurement_values: dict[str, tuple[float | None, str]] = {
        "PASS": (5.0, "MEASURED"),
        "FAIL": (11.0, "MEASURED"),
        "NO_MATCH": (5.0, "MEASURED"),
        "CONFIG_AMBIGUOUS": (5.0, "MEASURED"),
        "NOT_EVALUATED": (None, "MISSING"),
        "INVALID_VALUE": (5.0, "INVALID"),
    }
    connection.execute(
        text(
            "INSERT test.measurement(unit_id,test_item_id,value_numeric,raw_value,"
            "measurement_status,source_column_index) "
            "VALUES(:unit,:item,:value,:raw,:status,:column)"
        ),
        [
            {
                "unit": unit_id,
                "item": item_ids[step_code],
                "value": value,
                "raw": None if value is None else str(value),
                "status": status,
                "column": index,
            }
            for index, (step_code, (value, status)) in enumerate(
                measurement_values.items(), start=1
            )
        ],
    )
    return (
        processing_run_id,
        run_id,
        spec_set_id,
        binding_id,
        item_ids,
        supplier_id,
    )


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
    supplier_code = f"SPEC-E2E-{token}"
    captured_rows: list[dict[str, Any]] = []
    first_count = 0
    second_count = 0
    transaction = None
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            base_row = (
                connection.execute(
                    text(
                        "SELECT TOP (1) pr.job_id,pr.source_file_id,"
                        "pr.parser_profile_id FROM ingestion.processing_run pr "
                        "ORDER BY pr.processing_run_id"
                    )
                )
                .mappings()
                .one_or_none()
            )
            if base_row is None:
                raise RuntimeError("one existing Processing Run identity is required")
            base = dict(base_row)
            (
                processing_run_id,
                run_id,
                spec_set_id,
                binding_id,
                item_ids,
                _supplier_id,
            ) = _create_context(connection, base=base, token=token)

            materialize_processing_run_spec_evaluations(
                connection,
                processing_run_id=processing_run_id,
                explicit_run_spec_set_ids={run_id: spec_set_id},
            )
            first_count = _scalar(
                connection,
                "SELECT COUNT_BIG(*) FROM test.measurement_evaluation "
                "WHERE processing_run_id=:processing AND evaluation_type='SPEC' "
                "AND evaluation_scope_key=N'FORMAL_SPEC' AND is_current=1",
                {"processing": processing_run_id},
            )
            materialize_processing_run_spec_evaluations(
                connection,
                processing_run_id=processing_run_id,
                explicit_run_spec_set_ids={run_id: spec_set_id},
            )
            second_count = _scalar(
                connection,
                "SELECT COUNT_BIG(*) FROM test.measurement_evaluation "
                "WHERE processing_run_id=:processing AND evaluation_type='SPEC' "
                "AND evaluation_scope_key=N'FORMAL_SPEC' AND is_current=1",
                {"processing": processing_run_id},
            )
            rows = (
                connection.execute(
                    text(
                        "SELECT item.step_code,evaluation.spec_binding_id,"
                        "evaluation.spec_item_id,evaluation.lsl_applied,"
                        "evaluation.usl_applied,evaluation.lower_operator_applied,"
                        "evaluation.upper_operator_applied,"
                        "evaluation.evaluation_result,evaluation.evaluation_reason "
                        "FROM test.measurement_evaluation evaluation "
                        "JOIN test.measurement measurement "
                        "ON measurement.measurement_id=evaluation.measurement_id "
                        "JOIN mdm.test_item_definition item "
                        "ON item.test_item_id=measurement.test_item_id "
                        "WHERE evaluation.processing_run_id=:processing "
                        "AND evaluation.evaluation_type='SPEC' "
                        "AND evaluation.evaluation_scope_key=N'FORMAL_SPEC' "
                        "AND evaluation.is_current=1 ORDER BY item.sequence_no"
                    ),
                    {"processing": processing_run_id},
                )
                .mappings()
                .all()
            )
            captured_rows = [dict(row) for row in rows]
            expected = {
                "PASS",
                "FAIL",
                "NO_MATCH",
                "CONFIG_AMBIGUOUS",
                "NOT_EVALUATED",
                "INVALID_VALUE",
            }
            by_step = {str(row["step_code"]): row for row in captured_rows}
            if (
                first_count != 6
                or second_count != first_count
                or set(by_step) != expected
            ):
                raise AssertionError(
                    "formal Spec idempotence/status coverage failed: "
                    f"first={first_count}, second={second_count}, rows={captured_rows}"
                )
            if any(by_step[step]["evaluation_result"] != step for step in expected):
                raise AssertionError(
                    f"formal Spec classification failed: {captured_rows}"
                )
            for step, row in by_step.items():
                if int(row["spec_binding_id"]) != binding_id:
                    raise AssertionError(f"{step} binding snapshot failed: {row}")
                if step in {"NO_MATCH", "CONFIG_AMBIGUOUS"}:
                    if row["spec_item_id"] is not None:
                        raise AssertionError(
                            f"{step} leaked arbitrary Spec item: {row}"
                        )
                else:
                    if not (
                        int(row["spec_item_id"]) > 0
                        and float(row["lsl_applied"]) == 0.0
                        and float(row["usl_applied"]) == 10.0
                        and row["lower_operator_applied"] == ">="
                        and row["upper_operator_applied"] == "<="
                    ):
                        raise AssertionError(f"{step} Spec snapshot failed: {row}")
            if set(item_ids) != expected:
                raise AssertionError("fixture Test Item reconciliation failed")
        finally:
            if transaction is not None and transaction.is_active:
                transaction.rollback()

    with engine.connect() as connection:
        remaining = _scalar(
            connection,
            "SELECT COUNT_BIG(*) FROM mdm.supplier WHERE supplier_code=:code",
            {"code": supplier_code},
        )
    if remaining != 0:
        raise AssertionError("rollback verification fixture escaped its transaction")

    return {
        "database": identity["database"],
        "database_version": identity["database_version"],
        "schema_revision": identity["schema_revision"],
        "statuses": [row["evaluation_result"] for row in captured_rows],
        "evaluation_count_after_first_materialization": first_count,
        "evaluation_count_after_second_materialization": second_count,
        "rollback_clean": True,
    }


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
