from __future__ import annotations

import argparse
import getpass

import pyodbc

EXPECTED_SCHEMAS = {
    "mdm",
    "ingestion",
    "test",
    "trace",
    "governance",
    "analytics",
    "iam",
    "dataset",
    "evaluation",
    "analysis",
    "delivery",
    "workspace",
}
EXPECTED_VIEWS = {
    "analytics.v_current_dataset_version",
    "analytics.v_current_test_run",
    "analytics.v_current_unit_result",
    "analytics.v_current_measurement",
}
EXPECTED_TABLES = {
    "ingestion.initial_import_finalize_intent",
    "ingestion.processing_job",
    "ingestion.processing_input_request",
    "ingestion.processing_run",
    "ingestion.processing_run_input_file",
    "ingestion.worker_instance",
    "ingestion.format_profile",
    "ingestion.cleaner_release",
    "ingestion.field_enrichment",
    "ingestion.processing_artifact",
    "ingestion.lifecycle_job_target",
    "test.test_run",
    "test.unit_result",
    "test.measurement",
    "dataset.dataset",
    "dataset.dataset_version",
    "evaluation.evaluation_run",
    "delivery.export_job",
    "governance.audit_log",
    "workspace.analysis_session",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify TMS SQL Server 2014 schema")
    parser.add_argument("--server", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", default="TMS_G0_DEV")
    parser.add_argument("--driver", default="ODBC Driver 18 for SQL Server")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    password = getpass.getpass("SQL password: ")
    connection_string = (
        f"DRIVER={{{args.driver}}};SERVER={args.server};DATABASE={args.database};"
        f"UID={args.user};PWD={password};Encrypt=no;"
        "TrustServerCertificate=yes;Connection Timeout=8"
    )
    connection = pyodbc.connect(connection_string, autocommit=True)
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT version_num FROM alembic_version")
        revision = cursor.fetchone()[0]
        assert revision == "sql2014_0018", revision

        cursor.execute("SELECT name FROM sys.schemas")
        schemas = {row[0] for row in cursor.fetchall()}
        assert not (EXPECTED_SCHEMAS - schemas), EXPECTED_SCHEMAS - schemas

        cursor.execute("SELECT SCHEMA_NAME(schema_id) + '.' + name FROM sys.tables")
        tables = {row[0] for row in cursor.fetchall()}
        assert not (EXPECTED_TABLES - tables), EXPECTED_TABLES - tables

        cursor.execute(
            "SELECT name FROM sys.columns "
            "WHERE object_id=OBJECT_ID('dataset.dataset')"
        )
        dataset_columns = {row[0] for row in cursor.fetchall()}
        required_lifecycle_columns = {
            "lifecycle_status",
            "archived_at_utc",
            "archived_by_user_id",
            "archive_reason",
            "lifecycle_row_version",
        }
        assert not (required_lifecycle_columns - dataset_columns), (
            required_lifecycle_columns - dataset_columns
        )

        cursor.execute(
            "SELECT name FROM sys.columns "
            "WHERE object_id=OBJECT_ID('ingestion.lifecycle_job_target')"
        )
        lifecycle_target_columns = {row[0] for row in cursor.fetchall()}
        assert lifecycle_target_columns == {
            "job_id",
            "dataset_id",
            "target_dataset_version_id",
            "action_type",
            "requested_by_user_id",
            "request_reason",
            "created_at_utc",
            "row_version",
        }, lifecycle_target_columns
        cursor.execute(
            "SELECT definition FROM sys.check_constraints "
            "WHERE parent_object_id=OBJECT_ID('ingestion.processing_artifact') "
            "AND name='CK_processing_artifact_physical_status'"
        )
        artifact_status_check = cursor.fetchone()
        assert artifact_status_check is not None
        assert "DELETING" in artifact_status_check[0]

        cursor.execute("SELECT SCHEMA_NAME(schema_id) + '.' + name FROM sys.views")
        views = {row[0] for row in cursor.fetchall()}
        assert not (EXPECTED_VIEWS - views), EXPECTED_VIEWS - views

        cursor.execute(
            """
            SELECT i.name, i.type_desc
            FROM sys.indexes AS i
            WHERE i.object_id = OBJECT_ID('test.measurement')
              AND i.name IS NOT NULL
            """
        )
        indexes = {row[0]: row[1] for row in cursor.fetchall()}
        assert indexes.get("PK_measurement") == "CLUSTERED", indexes
        assert indexes.get("IX_measurement_unit") == "NONCLUSTERED", indexes
        assert all("COLUMNSTORE" not in value for value in indexes.values()), indexes

        cursor.execute(
            "SELECT c.name,c.is_nullable FROM sys.columns c "
            "WHERE c.object_id=OBJECT_ID('test.test_run') "
            "AND c.name IN('product_id','lot_id')"
        )
        identity_nullability = {row[0]: bool(row[1]) for row in cursor.fetchall()}
        assert identity_nullability == {"product_id": True, "lot_id": True}
        cursor.execute(
            "SELECT COUNT(*) FROM sys.check_constraints "
            "WHERE name IN('CK_test_program_stage_identity','CK_test_run_stage_identity')"
        )
        assert cursor.fetchone()[0] == 0

        cursor.execute(
            "SELECT COUNT(*) FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id "
            "WHERE s.name='analysis' AND t.name IN('run','unit','test_item','measurement')"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT COUNT(*) FROM sys.tables WHERE object_id=OBJECT_ID('analysis.saved_analysis')"
        )
        assert cursor.fetchone()[0] == 1

        cursor.execute(
            "SELECT name FROM sys.columns "
            "WHERE object_id=OBJECT_ID('ingestion.worker_instance')"
        )
        worker_columns = {row[0] for row in cursor.fetchall()}
        required_worker_columns = {
            "worker_id",
            "worker_kind",
            "state",
            "desired_state",
            "started_at_utc",
            "last_seen_at_utc",
            "stopped_at_utc",
            "database_name",
            "schema_revision",
            "host_fingerprint",
            "control_updated_at_utc",
            "row_version",
        }
        assert worker_columns == required_worker_columns, worker_columns
        cursor.execute(
            "SELECT COUNT(*) FROM sys.indexes "
            "WHERE object_id=OBJECT_ID('ingestion.worker_instance') "
            "AND name IN('IX_worker_instance_health','IX_worker_instance_control')"
        )
        assert cursor.fetchone()[0] == 2

        cursor.execute(
            "SELECT name FROM sys.columns WHERE object_id=OBJECT_ID('ingestion.processing_job')"
        )
        job_columns = {row[0] for row in cursor.fetchall()}
        required_job_columns = {
            "idempotency_key",
            "lease_token",
            "lease_owner",
            "lease_expires_at_utc",
            "heartbeat_at_utc",
            "attempt_count",
            "max_attempts",
            "analysis_session_id",
            "parent_job_id",
            "finalize_protocol",
        }
        assert not (required_job_columns - job_columns), (
            required_job_columns - job_columns
        )

        cursor.execute(
            "SELECT name FROM sys.columns "
            "WHERE object_id=OBJECT_ID('ingestion.processing_run_input_file')"
        )
        run_input_columns = {row[0] for row in cursor.fetchall()}
        required_run_input_columns = {
            "processing_run_id",
            "import_batch_file_id",
            "lineage_basis",
            "created_at_utc",
        }
        assert run_input_columns == required_run_input_columns, run_input_columns
        cursor.execute(
            "SELECT COUNT(*) FROM sys.indexes "
            "WHERE object_id=OBJECT_ID('ingestion.processing_run_input_file') "
            "AND name='IX_processing_run_input_file_batch_file'"
        )
        assert cursor.fetchone()[0] == 1

        cursor.execute(
            "SELECT name FROM sys.columns "
            "WHERE object_id=OBJECT_ID('ingestion.initial_import_finalize_intent')"
        )
        finalize_intent_columns = {row[0] for row in cursor.fetchall()}
        required_finalize_intent_columns = {
            "job_id",
            "import_batch_id",
            "processing_run_id",
            "dataset_version_id",
            "input_manifest_sha256",
            "input_manifest_json",
            "status",
            "staged_attempt_count",
            "staged_at_utc",
            "finalized_at_utc",
            "finalized_lease_token",
            "aborted_at_utc",
            "abort_error_code",
            "abort_error_message",
            "row_version",
        }
        assert finalize_intent_columns == required_finalize_intent_columns, (
            finalize_intent_columns
        )
        cursor.execute(
            "SELECT COUNT(*) FROM sys.indexes "
            "WHERE object_id=OBJECT_ID('ingestion.initial_import_finalize_intent') "
            "AND name='IX_initial_import_finalize_recovery'"
        )
        assert cursor.fetchone()[0] == 1

        cursor.execute(
            "SELECT name FROM sys.columns "
            "WHERE object_id=OBJECT_ID('ingestion.processing_input_request')"
        )
        input_request_columns = {row[0] for row in cursor.fetchall()}
        required_input_request_columns = {
            "job_id",
            "import_batch_id",
            "receipt_id",
            "field_code",
            "status",
            "prompt",
            "evidence_json",
            "resolved_enrichment_id",
            "resolved_by",
            "requested_at_utc",
            "resolved_at_utc",
        }
        assert not (required_input_request_columns - input_request_columns), (
            required_input_request_columns - input_request_columns
        )
        cursor.execute(
            "SELECT COUNT(*) FROM sys.indexes "
            "WHERE object_id=OBJECT_ID('ingestion.processing_input_request') "
            "AND name='UX_processing_input_request_open' AND has_filter=1"
        )
        assert cursor.fetchone()[0] == 1

        cursor.execute(
            "SELECT name FROM sys.columns "
            "WHERE object_id=OBJECT_ID('workspace.analysis_session')"
        )
        quick_columns = {row[0] for row in cursor.fetchall()}
        required_quick_columns = {
            "reserved_bytes",
            "cleanup_status",
            "cleanup_attempt_count",
            "cleanup_attempted_at_utc",
            "cleaned_at_utc",
            "cleanup_error",
        }
        assert not (required_quick_columns - quick_columns), (
            required_quick_columns - quick_columns
        )
        cursor.execute(
            "SELECT name FROM sys.columns "
            "WHERE object_id=OBJECT_ID('ingestion.processing_artifact')"
        )
        artifact_columns = {row[0] for row in cursor.fetchall()}
        required_artifact_columns = {
            "physical_status",
            "deletion_attempt_count",
            "deletion_attempted_at_utc",
            "deleted_at_utc",
            "deletion_error",
        }
        assert not (required_artifact_columns - artifact_columns), (
            required_artifact_columns - artifact_columns
        )

        cursor.execute(
            "SELECT name FROM sys.columns "
            "WHERE object_id=OBJECT_ID('dataset.dataset_version')"
        )
        dataset_version_columns = {row[0] for row in cursor.fetchall()}
        assert "spec_set_id" in dataset_version_columns
        cursor.execute(
            "SELECT name FROM sys.columns "
            "WHERE object_id=OBJECT_ID('ingestion.processing_result_summary')"
        )
        result_columns = {row[0] for row in cursor.fetchall()}
        assert {"dataset_id", "dataset_version_no"} <= result_columns

        cursor.execute(
            "SELECT COUNT(*) FROM sys.check_constraints "
            "WHERE definition LIKE '%ISJSON%'"
        )
        assert cursor.fetchone()[0] == 0

        cursor.execute("SELECT COUNT(*) FROM iam.role")
        role_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM iam.permission")
        permission_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM ingestion.data_quality_rule")
        dq_rule_count = cursor.fetchone()[0]
        assert role_count >= 7
        assert permission_count >= 11
        assert dq_rule_count >= 8

        print(f"revision={revision}")
        print(f"schemas={len(schemas)} tables={len(tables)} views={len(views)}")
        print(f"measurement_indexes={indexes}")
        print(f"stage_identity_nullability={identity_nullability}")
        print(f"route_a_job_columns={sorted(required_job_columns)}")
        print(
            f"seed_counts=roles:{role_count},permissions:{permission_count},"
            f"dq_rules:{dq_rule_count}"
        )
        print("schema_verification=PASS")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
