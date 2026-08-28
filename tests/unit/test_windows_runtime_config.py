from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMMON_SCRIPT = ROOT / "scripts" / "windows" / "TmsRuntime.Common.ps1"
LOCAL_COMMON_SCRIPT = ROOT / "scripts" / "windows" / "TmsLocalRuntime.Common.ps1"
PYTHON = ROOT / ".conda-env" / "python.exe"


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell runtime contract")


def _powershell_literal(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def test_runtime_config_is_loaded_as_utf8_without_bom(tmp_path: Path) -> None:
    runtime_config = tmp_path / "runtime.ps1"
    expected = r'[{"code":"FT_SOURCE","path":"F:\\测试数据\\日月新"}]'
    runtime_config.write_text(
        f"$env:TMS_UTF8_TEST = '{expected}'\n",
        encoding="utf-8",
    )
    command = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        f". {_powershell_literal(COMMON_SCRIPT)}; "
        f"Import-TmsRuntimeConfig -Path {_powershell_literal(runtime_config)}; "
        "[Console]::Write($env:TMS_UTF8_TEST)"
    )

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.stdout == expected


def test_runtime_config_rejects_invalid_utf8(tmp_path: Path) -> None:
    runtime_config = tmp_path / "invalid-runtime.ps1"
    runtime_config.write_bytes(b"$env:TMS_TEST = '\xff'\n")
    command = (
        f". {_powershell_literal(COMMON_SCRIPT)}; "
        f"Import-TmsRuntimeConfig -Path {_powershell_literal(runtime_config)}"
    )

    completed = subprocess.run(
        [
            "powershell.exe",
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

    assert completed.returncode != 0
    assert "Runtime configuration must be valid UTF-8" in completed.stderr


def test_local_database_guard_rejects_non_dev_database() -> None:
    command = (
        f". {_powershell_literal(LOCAL_COMMON_SCRIPT)}; "
        "$env:TMS_DATABASE_URL = "
        "'mssql+pyodbc://localhost/TMS_PROD?driver=ODBC+Driver+17+for+SQL+Server'; "
        f"Assert-TmsLocalDatabaseGuard -Python {_powershell_literal(PYTHON)} "
        "-ExpectedDatabase 'TMS_G0_DEV'"
    )

    completed = subprocess.run(
        [
            "powershell.exe",
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

    assert completed.returncode != 0
    assert "database guard rejected configured database 'TMS_PROD'" in completed.stderr


def test_frontend_environment_scrub_hides_and_restores_tms_secrets() -> None:
    command = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        f". {_powershell_literal(LOCAL_COMMON_SCRIPT)}; "
        "$env:TMS_DATABASE_URL = 'sensitive-value'; "
        "$env:PYTHONPATH = 'sensitive-path'; "
        "$inside = Invoke-WithoutTmsEnvironment -Action { "
        "if ((Test-Path Env:TMS_DATABASE_URL) -or (Test-Path Env:PYTHONPATH)) "
        "{ 'LEAKED' } else { 'CLEAN' } }; "
        "[Console]::Write("
        "$inside + '|' + $env:TMS_DATABASE_URL + '|' + $env:PYTHONPATH)"
    )

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.stdout == "CLEAN|sensitive-value|sensitive-path"


def test_process_record_upsert_preserves_unprocessed_roles() -> None:
    command = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        f". {_powershell_literal(LOCAL_COMMON_SCRIPT)}; "
        "$state = [PSCustomObject]@{ processes = @("
        "[PSCustomObject]@{ role='api'; process_id=1 },"
        "[PSCustomObject]@{ role='frontend'; process_id=2 },"
        "[PSCustomObject]@{ role='worker'; process_id=3 }) }; "
        "$replacement = [PSCustomObject]@{ role='api'; process_id=9 }; "
        "Set-TmsStateProcessRecord -State $state -Record $replacement; "
        "$summary = @($state.processes | Sort-Object role | ForEach-Object "
        "{ $_.role + ':' + $_.process_id }) -join ','; "
        "[Console]::Write($summary)"
    )

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.stdout == "api:9,frontend:2,worker:3"
