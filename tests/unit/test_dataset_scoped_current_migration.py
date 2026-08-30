from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell.exe")


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_maintenance_contract(
    *, reverse_mismatch_count: int
) -> subprocess.CompletedProcess[str]:
    maintenance = ROOT / "scripts" / "windows" / "TmsDatabaseMaintenance.Common.ps1"
    columns = (
        "invalid_dataset_current",
        "duplicate_dataset_current",
        "invalid_run_current",
        "current_dataset_without_run",
        "current_dataset_run_mismatch",
        "run_without_current_dataset_state_mismatch",
    )
    issue_table = ";".join(
        f"[void]$table.Columns.Add('{name}',[Int64])" for name in columns
    )
    issue_values = ";".join(
        f"$row.{name}=" + (str(reverse_mismatch_count) if name == columns[-1] else "0")
        for name in columns
    )
    command = (
        f". {_ps_literal(maintenance)};"
        "function Invoke-TmsSqlTable { param("
        "[string]$SqlInstance,[string]$Database,[string]$Sql,"
        "[hashtable]$Parameters=@{},[int]$CommandTimeout=30,"
        "[switch]$TrustServerCertificate);"
        "if($Sql -like '*alembic_version*'){"
        "$table=New-Object System.Data.DataTable;"
        "[void]$table.Columns.Add('version_num',[string]);"
        "$row=$table.NewRow();$row.version_num='sql2014_0019';"
        "[void]$table.Rows.Add($row);return ,$table};"
        "$table=New-Object System.Data.DataTable;"
        f"{issue_table};$row=$table.NewRow();{issue_values};"
        "[void]$table.Rows.Add($row);return ,$table};"
        "$result=Test-TmsDatabaseConsistency -SqlInstance 'SQLTEST' "
        "-Database 'TMS_TEST' -ExpectedSchemaRevision 'sql2014_0019';"
        "$result | ConvertTo-Json -Compress"
    )
    assert POWERSHELL is not None
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_dataset_scoped_current_migration_removes_only_source_unique_invariant() -> (
    None
):
    sql = (
        ROOT / "db" / "alembic" / "sql" / "0019_dataset_scoped_current_sql2014.sql"
    ).read_text(encoding="utf-8-sig")

    assert "SET XACT_ABORT ON" in sql
    assert "UX_dataset_version_current" in sql
    assert "DROP INDEX UX_processing_run_current ON ingestion.processing_run" in sql
    assert "CREATE NONCLUSTERED INDEX IX_processing_run_source_state" in sql
    assert "CREATE UNIQUE NONCLUSTERED INDEX IX_processing_run_source_state" not in sql
    assert "GROUP BY source_file_id" not in sql
    assert "CREATE OR ALTER" not in sql
    assert "UPDATE ingestion.processing_run" not in sql
    assert "UPDATE dataset.dataset_version" not in sql
    assert "DELETE FROM" not in sql


def test_dataset_scoped_current_alembic_wrapper_follows_0018_and_is_irreversible() -> (
    None
):
    wrapper = (
        ROOT / "db" / "alembic" / "versions" / "sql2014_0019_dataset_scoped_current.py"
    ).read_text(encoding="utf-8-sig")

    assert 'revision = "sql2014_0019"' in wrapper
    assert 'down_revision = "sql2014_0018"' in wrapper
    assert 'run_sql_file("0019_dataset_scoped_current_sql2014.sql")' in wrapper
    assert "irreversible_downgrade()" in wrapper


def test_windows_maintenance_checks_dataset_links_not_source_singletons() -> None:
    maintenance = (
        ROOT / "scripts" / "windows" / "TmsDatabaseMaintenance.Common.ps1"
    ).read_text(encoding="utf-8-sig")

    assert "current_dataset_without_run" in maintenance
    assert "current_dataset_run_mismatch" in maintenance
    assert "run_without_current_dataset_state_mismatch" in maintenance
    assert "pr.status IN('PUBLISHED','SUPERSEDED')" in maintenance
    assert "(pr.status<>'SUPERSEDED' OR pr.is_current<>0)" in maintenance
    assert "RunWithoutCurrentDatasetStateMismatchIssues" in maintenance
    assert "GROUP BY source_file_id" not in maintenance
    assert "duplicate_run_current" not in maintenance


@pytest.mark.skipif(os.name != "nt", reason="Windows SQL maintenance contract")
def test_windows_maintenance_reports_zero_reverse_alignment_issues() -> None:
    completed = _run_maintenance_contract(reverse_mismatch_count=0)

    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(completed.stdout.strip())
    assert payload["RunWithoutCurrentDatasetStateMismatchIssues"] == 0
    assert payload["ProcessingRunCurrentIssues"] == 0
    assert payload["TotalIssues"] == 0
    assert payload["Status"] == "CONSISTENT"


@pytest.mark.skipif(os.name != "nt", reason="Windows SQL maintenance contract")
def test_windows_maintenance_rejects_published_noncurrent_run_without_current_dataset() -> (
    None
):
    completed = _run_maintenance_contract(reverse_mismatch_count=1)

    assert completed.returncode != 0
    output = completed.stderr + completed.stdout
    assert "total=1" in output
    assert "run_without_current_dataset_state_mismatch=1" in output
