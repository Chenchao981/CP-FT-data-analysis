from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMMON_SCRIPT = ROOT / "scripts" / "windows" / "TmsRuntime.Common.ps1"
LOCAL_COMMON_SCRIPT = ROOT / "scripts" / "windows" / "TmsLocalRuntime.Common.ps1"
PYTHON = ROOT / ".conda-env" / "python.exe"
LOCAL_LIFECYCLE_SCRIPTS = (
    ROOT / "scripts" / "windows" / "start_tms_local_test.ps1",
    ROOT / "scripts" / "windows" / "get_tms_local_test_status.ps1",
    ROOT / "scripts" / "windows" / "stop_tms_local_test.ps1",
)


pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows PowerShell runtime contract"
)


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


def test_external_runtime_contract_accepts_only_release_external_paths(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    runtime_home = tmp_path / "runtime-home"
    config_dir = tmp_path / "config"
    python_dir = tmp_path / "python"
    for directory in (release, runtime_home, config_dir, python_dir):
        directory.mkdir()
    config = config_dir / "runtime.ps1"
    python = python_dir / "python.exe"
    config.write_text("$env:TMS_JOB_REPOSITORY='sql'\n", encoding="utf-8")
    python.write_bytes(b"runtime-placeholder")
    command = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        f". {_powershell_literal(COMMON_SCRIPT)}; "
        "$contract = Resolve-TmsExternalRuntimeContract "
        f"-Workspace {_powershell_literal(release)} "
        f"-RuntimeHome {_powershell_literal(runtime_home)} "
        f"-RuntimeConfigPath {_powershell_literal(config)} "
        f"-PythonPath {_powershell_literal(python)}; "
        "[Console]::Write((Test-Path -LiteralPath $contract.RuntimeHome "
        "-PathType Container).ToString())"
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

    assert completed.stdout == "True"


def test_external_runtime_contract_rejects_path_inside_release(tmp_path: Path) -> None:
    release = tmp_path / "release"
    runtime_home = release / "runtime-home"
    config_dir = tmp_path / "config"
    python_dir = tmp_path / "python"
    for directory in (runtime_home, config_dir, python_dir):
        directory.mkdir(parents=True)
    config = config_dir / "runtime.ps1"
    python = python_dir / "python.exe"
    config.write_text("", encoding="utf-8")
    python.write_bytes(b"runtime-placeholder")
    command = (
        f". {_powershell_literal(COMMON_SCRIPT)}; "
        "Resolve-TmsExternalRuntimeContract "
        f"-Workspace {_powershell_literal(release)} "
        f"-RuntimeHome {_powershell_literal(runtime_home)} "
        f"-RuntimeConfigPath {_powershell_literal(config)} "
        f"-PythonPath {_powershell_literal(python)}"
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
    assert "must be outside the Release root" in completed.stderr


def test_external_runtime_contract_rejects_volume_root(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    root = Path(release.anchor)
    command = (
        f". {_powershell_literal(COMMON_SCRIPT)}; "
        "Resolve-TmsExternalRuntimePath -Name 'RuntimeHome' "
        f"-Path {_powershell_literal(root)} -PathType Directory "
        f"-Workspace {_powershell_literal(release)}"
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
    assert "cannot be a volume or share root" in completed.stderr
    common = COMMON_SCRIPT.read_text(encoding="utf-8-sig")
    assert "Assert-TmsNoReparsePath -Name $Name -Path $full" in common


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


def test_configured_local_authentication_rejects_non_true_value() -> None:
    command = (
        f". {_powershell_literal(LOCAL_COMMON_SCRIPT)}; "
        "$env:TMS_AUTH_REQUIRED = 'false'; "
        "Resolve-TmsLocalAuthenticationContract -UseConfiguredAuthentication"
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
    assert "requires TMS_AUTH_REQUIRED=true" in completed.stderr


def test_local_authentication_contract_returns_exact_boolean_modes() -> None:
    command = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        f". {_powershell_literal(LOCAL_COMMON_SCRIPT)}; "
        "$env:TMS_AUTH_REQUIRED = 'yes'; "
        "$configured = Resolve-TmsLocalAuthenticationContract "
        "-UseConfiguredAuthentication; "
        "$disabled = Resolve-TmsLocalAuthenticationContract; "
        "[Console]::Write($configured.Mode + '|' + $configured.AuthRequired + "
        "'|' + $disabled.Mode + '|' + $disabled.AuthRequired + '|' + "
        "$env:TMS_AUTH_REQUIRED)"
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

    assert completed.stdout == ("CONFIGURED|True|DISABLED_ON_LOOPBACK|False|false")


def test_local_start_validates_auth_before_validate_only_and_status_exposes_it() -> (
    None
):
    start = LOCAL_LIFECYCLE_SCRIPTS[0].read_text(encoding="utf-8-sig")
    status = LOCAL_LIFECYCLE_SCRIPTS[1].read_text(encoding="utf-8-sig")

    assert start.index("Resolve-TmsLocalAuthenticationContract") < start.index(
        "if ($ValidateOnly)"
    )
    assert start.count("auth_required = $authRequired") == 2
    assert "auth_required = $authRequired" in status


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


def test_local_lifecycle_scripts_use_the_shared_utf8_json_reader() -> None:
    for script_path in LOCAL_LIFECYCLE_SCRIPTS:
        script = script_path.read_text(encoding="utf-8-sig")
        assert "Read-TmsLocalJsonFile -Path $statePath" in script
        assert (
            "Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json" not in script
        )

    start_script = LOCAL_LIFECYCLE_SCRIPTS[0].read_text(encoding="utf-8-sig")
    status_script = LOCAL_LIFECYCLE_SCRIPTS[1].read_text(encoding="utf-8-sig")
    assert "Read-TmsLocalJsonFile -Path $workerReadyFile" in start_script
    assert "Read-TmsLocalJsonFile -Path $workerReadyFile" in status_script


def test_local_state_cross_reads_between_windows_powershell_and_powershell_core(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh.exe")
    if pwsh is None:
        pytest.skip("PowerShell Core is unavailable")
    windows_powershell = shutil.which("powershell.exe")
    assert windows_powershell is not None
    state_path = tmp_path / "processes.json"
    write_command = (
        f". {_powershell_literal(LOCAL_COMMON_SCRIPT)}; "
        "$state = [PSCustomObject]@{ workspace='F:\\CP-FT数据分析'; "
        "status='RUNNING'; processes=@() }; "
        f"Write-TmsLocalState -StatePath {_powershell_literal(state_path)} -State $state"
    )
    read_command = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        f". {_powershell_literal(LOCAL_COMMON_SCRIPT)}; "
        f"$state = Read-TmsLocalJsonFile -Path {_powershell_literal(state_path)}; "
        "[Console]::Write($state.workspace + '|' + $state.status)"
    )

    for writer, reader in (
        (pwsh, windows_powershell),
        (windows_powershell, pwsh),
    ):
        subprocess.run(
            [writer, "-NoProfile", "-NonInteractive", "-Command", write_command],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        completed = subprocess.run(
            [reader, "-NoProfile", "-NonInteractive", "-Command", read_command],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert completed.stdout == "F:\\CP-FT数据分析|RUNNING"


def test_local_json_reader_rejects_invalid_utf8(tmp_path: Path) -> None:
    state_path = tmp_path / "invalid.json"
    state_path.write_bytes(b'{"workspace":"\xff"}')
    command = (
        f". {_powershell_literal(LOCAL_COMMON_SCRIPT)}; "
        f"Read-TmsLocalJsonFile -Path {_powershell_literal(state_path)}"
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
    assert "Local TMS JSON file must be valid UTF-8" in completed.stderr


@pytest.mark.parametrize("shell_name", ["powershell.exe", "pwsh.exe"])
def test_local_private_directory_works_in_both_powershell_hosts(
    tmp_path: Path, shell_name: str
) -> None:
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} is unavailable")
    private_directory = tmp_path / "private-state"
    command = (
        f". {_powershell_literal(LOCAL_COMMON_SCRIPT)}; "
        f"Set-TmsLocalPrivateDirectory -Path {_powershell_literal(private_directory)}; "
        f"if (-not (Test-Path -LiteralPath {_powershell_literal(private_directory)} "
        "-PathType Container)) { throw 'private directory missing' }"
    )

    completed = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert completed.returncode == 0, completed.stderr
