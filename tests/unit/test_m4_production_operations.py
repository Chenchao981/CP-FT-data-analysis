from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import scripts.release.build_tms_release as release_builder
from scripts.release.build_tms_release import (
    ReleaseValidationError,
    _validate_ready_payload,
    build_release,
    get_schema_head,
    inspect_release_archive,
)

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell.exe")
WINDOWS_SCRIPTS = ROOT / "scripts" / "windows"
M4_POWERSHELL_FILES = (
    WINDOWS_SCRIPTS / "TmsRuntime.Common.ps1",
    WINDOWS_SCRIPTS / "test_tms_production_preflight.ps1",
    WINDOWS_SCRIPTS / "test_tms_runtime_health.ps1",
    WINDOWS_SCRIPTS / "TmsDatabaseMaintenance.Common.ps1",
    WINDOWS_SCRIPTS / "backup_tms_database.ps1",
    WINDOWS_SCRIPTS / "restore_tms_database.ps1",
    WINDOWS_SCRIPTS / "test_tms_migration_readiness.ps1",
    WINDOWS_SCRIPTS / "invoke_tms_empty_database_migration_smoke.ps1",
    WINDOWS_SCRIPTS / "install_tms_scheduled_tasks.ps1",
    WINDOWS_SCRIPTS / "get_tms_scheduled_task_status.ps1",
    WINDOWS_SCRIPTS / "uninstall_tms_scheduled_tasks.ps1",
    WINDOWS_SCRIPTS / "run_tms_formal_cleanup.ps1",
    WINDOWS_SCRIPTS / "run_tms_analytics_export_worker.ps1",
    WINDOWS_SCRIPTS / "run_tms_analytics_export_cleanup.ps1",
    WINDOWS_SCRIPTS / "start_tms_local_agent.ps1",
    WINDOWS_SCRIPTS / "start_tms_runtime.ps1",
    ROOT / "docs" / "examples" / "TMS.production.runtime.example.ps1",
)


def _minimal_release_source(tmp_path: Path) -> Path:
    root = tmp_path / "release-source"
    for relative in sorted(
        release_builder.ROOT_FILES | release_builder.EXPLICIT_FILES
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# release discovery fixture\n", encoding="utf-8")
    for prefix, _suffixes in release_builder.ROOT_PREFIX_RULES:
        (root / prefix).mkdir(parents=True, exist_ok=True)
    launcher = root / "scripts" / "windows" / "start_tms_runtime.ps1"
    launcher.write_text("# production launcher fixture\n", encoding="utf-8")
    return root


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(
    *,
    command: str | None = None,
    file: Path | None = None,
    arguments: tuple[str, ...] = (),
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:
        pytest.skip("Windows PowerShell is unavailable")
    invocation = [
        POWERSHELL,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
    ]
    if command is not None:
        invocation.extend(("-Command", command))
    elif file is not None:
        invocation.extend(("-File", str(file), *arguments))
    else:
        raise AssertionError("command or file is required")
    return subprocess.run(
        invocation,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )


def _write_production_runtime(tmp_path: Path, *, overlap: bool = False) -> Path:
    directories = {
        name: tmp_path / name
        for name in ("source", "upload", "work", "quick", "analytics", "logs")
    }
    for directory in directories.values():
        directory.mkdir()
    if overlap:
        directories["quick"] = directories["work"]
    source_json = json.dumps(
        [
            {
                "code": "FT_SOURCE",
                "name": "Approved FT Source",
                "path": str(directories["source"]),
                "purpose": "FORMAL_IMPORT",
                "data_domain_code": "RIYUEXIN_FT",
                "business_domains": ["ENGINEERING"],
                "test_stage": "FT",
                "factory_code": "RIYUEXIN",
                "allowed_suffixes": [".xlsx"],
            }
        ],
        ensure_ascii=False,
    )
    values = {
        "TMS_ENV": "production",
        "TMS_AUTH_REQUIRED": "true",
        "TMS_JOB_REPOSITORY": "sql",
        "TMS_DATABASE_URL": (
            "mssql+pyodbc://@SQLPROD01/NCE_TMS?"
            "driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes"
        ),
        "TMS_EXPECTED_DATABASE": "NCE_TMS",
        "TMS_EXPECTED_DATABASE_SERVER": "SQLPROD01",
        "TMS_EXPECTED_SCHEMA_REVISION": get_schema_head(ROOT),
        "TMS_WORKER_ID": "route-a-prod-01",
        "TMS_WORKER_READY_FILE": str(tmp_path / "route-a.ready.json"),
        "TMS_LOG_RETENTION_DAYS": "30",
        "TMS_SOURCE_ROOTS_JSON": source_json,
        "TMS_UPLOAD_ROOT": str(directories["upload"]),
        "TMS_WORK_ROOT": str(directories["work"]),
        "TMS_QUICK_WORK_ROOT": str(directories["quick"]),
        "TMS_ANALYTICS_EXPORT_ROOT": str(directories["analytics"]),
        "TMS_LOG_DIR": str(directories["logs"]),
    }
    runtime = tmp_path / "runtime.ps1"
    runtime.write_text(
        "\n".join(
            f"$env:{name} = {_ps_literal(value)}" for name, value in values.items()
        )
        + "\n",
        encoding="utf-8",
    )
    return runtime


@pytest.mark.skipif(os.name != "nt", reason="Windows production runtime contract")
def test_m4_powershell_files_have_valid_ast() -> None:
    files = ",".join(_ps_literal(path) for path in M4_POWERSHELL_FILES)
    completed = _run_powershell(
        command=(
            f"$files=@({files}); $all=@(); foreach($file in $files){{"
            "$tokens=$null;$errors=$null;"
            "[void][System.Management.Automation.Language.Parser]::ParseFile("
            "$file,[ref]$tokens,[ref]$errors);$all+=@($errors)};"
            "if($all.Count -gt 0){$all|ForEach-Object{$_.Message};exit 1}"
        )
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows production runtime contract")
def test_production_preflight_accepts_strong_separated_runtime(tmp_path: Path) -> None:
    runtime = _write_production_runtime(tmp_path)
    environment = os.environ.copy()
    environment["TMS_JWT_SECRET"] = "A9" * 32
    environment["TMS_HEALTH_BEARER_TOKEN"] = "B8" * 24

    completed = _run_powershell(
        file=WINDOWS_SCRIPTS / "test_tms_production_preflight.ps1",
        arguments=("-RuntimeConfig", str(runtime), "-SkipAclCheck"),
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "VALID" in completed.stdout
    assert "sql2014_0028" in completed.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows production runtime contract")
def test_production_preflight_rejects_overlapping_roots(tmp_path: Path) -> None:
    runtime = _write_production_runtime(tmp_path, overlap=True)
    environment = os.environ.copy()
    environment["TMS_JWT_SECRET"] = "C7" * 32
    environment["TMS_HEALTH_BEARER_TOKEN"] = "D6" * 24

    completed = _run_powershell(
        file=WINDOWS_SCRIPTS / "test_tms_production_preflight.ps1",
        arguments=("-RuntimeConfig", str(runtime), "-SkipAclCheck"),
        environment=environment,
    )

    assert completed.returncode != 0
    assert "must not overlap" in completed.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows production runtime contract")
def test_production_config_rejects_literal_secret(tmp_path: Path) -> None:
    runtime = tmp_path / "unsafe.ps1"
    runtime.write_text(
        "$env:TMS_JWT_SECRET = 'literal-secret-value'\n", encoding="utf-8"
    )
    common = WINDOWS_SCRIPTS / "TmsRuntime.Common.ps1"
    completed = _run_powershell(
        command=(
            f". {_ps_literal(common)};"
            f"Assert-TmsRuntimeConfigContainsNoSecretLiterals -Path {_ps_literal(runtime)}"
        )
    )
    assert completed.returncode != 0
    assert "must not contain a literal JWT" in completed.stderr


def test_python_production_config_requires_exact_release_head(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.core.config import get_settings

    directories = {
        name: tmp_path / name
        for name in ("source", "upload", "work", "quick", "analytics", "logs")
    }
    for directory in directories.values():
        directory.mkdir()
    values = {
        "TMS_ENV": "production",
        "TMS_AUTH_REQUIRED": "true",
        "TMS_JOB_REPOSITORY": "sql",
        "TMS_JWT_SECRET": "E5" * 32,
        "TMS_DATABASE_URL": (
            "mssql+pyodbc://@SQLPROD01/NCE_TMS?"
            "driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes"
        ),
        "TMS_EXPECTED_DATABASE": "NCE_TMS",
        "TMS_EXPECTED_DATABASE_SERVER": "SQLPROD01",
        "TMS_EXPECTED_SCHEMA_REVISION": get_schema_head(ROOT),
        "TMS_UPLOAD_ROOT": str(directories["upload"]),
        "TMS_WORK_ROOT": str(directories["work"]),
        "TMS_QUICK_WORK_ROOT": str(directories["quick"]),
        "TMS_ANALYTICS_EXPORT_ROOT": str(directories["analytics"]),
        "TMS_LOG_DIR": str(directories["logs"]),
        "TMS_SOURCE_ROOTS_JSON": json.dumps([{"path": str(directories["source"])}]),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        assert get_settings().environment == "production"
        get_settings.cache_clear()
        monkeypatch.setenv("TMS_EXPECTED_SCHEMA_REVISION", "sql2014_0017")
        with pytest.raises(RuntimeError, match="release head"):
            get_settings()
        get_settings.cache_clear()
        monkeypatch.setenv("TMS_EXPECTED_SCHEMA_REVISION", get_schema_head(ROOT))
        monkeypatch.delenv("TMS_ANALYTICS_EXPORT_ROOT")
        with pytest.raises(RuntimeError, match="TMS_ANALYTICS_EXPORT_ROOT"):
            get_settings()
    finally:
        get_settings.cache_clear()


@pytest.mark.skipif(os.name != "nt", reason="Windows production probe contract")
def test_runtime_health_probe_matches_api_and_worker_identity(tmp_path: Path) -> None:
    runtime = _write_production_runtime(tmp_path)
    token = "F4" * 24
    expected_worker = "route-a-prod-01"
    ready_file = tmp_path / "route-a.ready.json"
    ready_file.write_text(
        json.dumps(
            {
                "status": "READY",
                "pid": os.getpid(),
                "worker_id": expected_worker,
                "database": "NCE_TMS",
                "schema_revision": get_schema_head(ROOT),
                "database_server": "SQLPROD01",
            }
        ),
        encoding="utf-8",
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/api/v1/health/ready":
                payload = {
                    "status": "ready",
                    "database": "NCE_TMS",
                    "schema_revision": get_schema_head(ROOT),
                    "database_server": "SQLPROD01",
                }
            elif self.path.startswith("/api/v1/operations/workers?"):
                if self.headers.get("Authorization") != f"Bearer {token}":
                    self.send_response(403)
                    self.end_headers()
                    return
                payload = {
                    "workers": [
                        {
                            "worker_id": expected_worker,
                            "state": "READY",
                            "is_stale": False,
                            "database_name": "NCE_TMS",
                            "schema_revision": get_schema_head(ROOT),
                        }
                    ]
                }
            else:
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = os.environ.copy()
    environment["TMS_JWT_SECRET"] = "G3" * 32
    environment["TMS_HEALTH_BEARER_TOKEN"] = token
    try:
        completed = _run_powershell(
            file=WINDOWS_SCRIPTS / "test_tms_runtime_health.ps1",
            arguments=(
                "-RuntimeConfig",
                str(runtime),
                "-ApiReadyUrl",
                f"http://127.0.0.1:{server.server_port}/api/v1/health/ready",
            ),
            environment=environment,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "VALID" in completed.stdout
    assert "WorkerRegistryChecked" in completed.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows SQL maintenance contract")
def test_database_maintenance_scripts_default_to_dry_run(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    restore_dir = tmp_path / "restore"
    backup_dir.mkdir()
    restore_dir.mkdir()
    backup_target = backup_dir / "new.bak"
    backup_source = backup_dir / "verified.bak"
    backup_source.write_bytes(b"not-connected-dry-run")

    backup = _run_powershell(
        file=WINDOWS_SCRIPTS / "backup_tms_database.ps1",
        arguments=(
            "-SqlInstance",
            "SQLPROD01",
            "-Database",
            "NCE_TMS",
            "-AllowedDatabases",
            "NCE_TMS",
            "-BackupPath",
            str(backup_target),
        ),
    )
    restore = _run_powershell(
        file=WINDOWS_SCRIPTS / "restore_tms_database.ps1",
        arguments=(
            "-SqlInstance",
            "SQLUAT01",
            "-TargetDatabase",
            "NCE_TMS_DR_TEST",
            "-AllowedTestDatabases",
            "NCE_TMS_DR_TEST",
            "-ProductionDatabases",
            "NCE_TMS",
            "-BackupPath",
            str(backup_source),
            "-RestoreDataDirectory",
            str(restore_dir),
            "-ExpectedSchemaRevision",
            get_schema_head(ROOT),
        ),
    )
    migration = _run_powershell(
        file=WINDOWS_SCRIPTS / "test_tms_migration_readiness.ps1",
        arguments=(
            "-SqlInstance",
            "SQLPROD01",
            "-Database",
            "NCE_TMS",
            "-AllowedDatabases",
            "NCE_TMS",
            "-Phase",
            "PostMigration",
            "-ExpectedSchemaRevision",
            get_schema_head(ROOT),
        ),
    )
    empty_migration = _run_powershell(
        file=WINDOWS_SCRIPTS / "invoke_tms_empty_database_migration_smoke.ps1",
        arguments=(
            "-SqlInstance",
            "SQLUAT01",
            "-Database",
            "NCE_TMS_MIGRATION_TEST",
            "-AllowedTestDatabases",
            "NCE_TMS_MIGRATION_TEST",
            "-ProductionDatabases",
            "NCE_TMS",
            "-ExpectedSchemaRevision",
            get_schema_head(ROOT),
        ),
    )

    for completed in (backup, restore, migration, empty_migration):
        assert completed.returncode == 0, completed.stderr + completed.stdout
        assert "DryRun" in completed.stdout
        assert "VALIDATED" in completed.stdout


def test_pre_migration_check_handles_schema_before_finalize_intent_table() -> None:
    migration_check = (WINDOWS_SCRIPTS / "test_tms_migration_readiness.ps1").read_text(
        encoding="utf-8"
    )

    assert "OBJECT_ID(N'ingestion.initial_import_finalize_intent'" in migration_check
    assert "EXEC sys.sp_executesql" in migration_check
    assert "@count=@staged_intents OUTPUT" in migration_check


def test_scheduled_tasks_keep_formal_and_quick_cleanup_separate() -> None:
    installer = (WINDOWS_SCRIPTS / "install_tms_scheduled_tasks.ps1").read_text(
        encoding="utf-8"
    )
    status = (WINDOWS_SCRIPTS / "get_tms_scheduled_task_status.ps1").read_text(
        encoding="utf-8"
    )
    uninstaller = (WINDOWS_SCRIPTS / "uninstall_tms_scheduled_tasks.ps1").read_text(
        encoding="utf-8"
    )
    formal_wrapper = (WINDOWS_SCRIPTS / "run_tms_formal_cleanup.ps1").read_text(
        encoding="utf-8"
    )
    formal_runner = (ROOT / "scripts" / "run_formal_artifact_cleanup.py").read_text(
        encoding="utf-8"
    )

    assert "[string]$FormalCleanupMode = 'DryRun'" in installer
    assert "TMS-QuickCleanup" in installer
    assert "TMS-FormalCleanup" in installer
    assert "TMS-AnalyticsExportWorker" in installer
    assert "start_tms_runtime.ps1" in installer
    assert "-RuntimeHome" in installer
    assert "-RuntimeConfigPath" in installer
    assert "-PythonPath" in installer
    assert "run_tms_cleanup.ps1" in installer
    assert "run_tms_formal_cleanup.ps1" in installer
    assert "TMS-FormalCleanup" in status
    assert "TMS-AnalyticsExportWorker" in status
    assert "ACTION_ROLE" in status
    assert "EXTERNAL_" in status
    assert "ExpectedFormalCleanupMode = 'DryRun'" in status
    assert "ExpectedCleanupMode = 'DryRun'" in status
    assert "CLEANUP_MODE" in status
    assert "TMS-FormalCleanup" in uninstaller
    assert "TMS-AnalyticsExportWorker" in uninstaller
    assert "scripts\\run_formal_artifact_cleanup.py" in formal_wrapper
    assert "if ($Delete)" in formal_wrapper
    assert "$arguments += '--delete'" in formal_wrapper
    assert "TMS_QUICK_WORK_ROOT" not in formal_wrapper
    assert "FormalOrphanRootCleaner" in formal_runner
    assert "SqlFormalOrphanCleanupService" in formal_runner
    assert 'TMS_FORMAL_ORPHAN_RETENTION_HOURS", "168"' in formal_runner
    assert "dry_run=dry_run" in formal_runner


@pytest.mark.skipif(os.name != "nt", reason="Windows release launcher contract")
def test_release_is_reproducible_inspected_and_launcher_smoked(tmp_path: Path) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    first_manifest = build_release(
        ROOT,
        first,
        release_version="v1.0-core-test",
        smoke_launcher=True,
    )
    second_manifest = build_release(
        ROOT,
        second,
        release_version="v1.0-core-test",
        smoke_launcher=True,
    )

    assert (
        hashlib.sha256(first.read_bytes()).digest()
        == hashlib.sha256(second.read_bytes()).digest()
    )
    assert first_manifest == second_manifest
    assert first_manifest["schema_revision"] == "sql2014_0028"
    inspected = inspect_release_archive(first)
    assert inspected == first_manifest
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
    assert ".env.runtime.ps1" not in names
    assert not any(name.startswith("data/") for name in names)
    assert not any(".test." in name.casefold() for name in names)
    assert not any(".spec." in name.casefold() for name in names)
    assert not any(name in release_builder.WINDOWS_LOCAL_ACCEPTANCE_FILES for name in names)
    assert {
        "local_agent/README.md",
        "local_agent/__init__.py",
        "local_agent/__main__.py",
        "local_agent/app.py",
        "local_agent/config.example.json",
        "local_agent/config.py",
        "local_agent/errors.py",
        "local_agent/manifest.py",
        "local_agent/models.py",
        "local_agent/runner.py",
        "local_agent/service.py",
        "scripts/windows/start_tms_local_agent.ps1",
    }.issubset(names)
    assert not any(name.startswith("local_agent/tests/") for name in names)
    assert not any("/__pycache__/" in name for name in names)
    assert "local_agent/config.json" not in names
    assert {
        "scripts/windows/get_tms_scheduled_task_status.ps1",
        "scripts/windows/install_tms_scheduled_tasks.ps1",
        "scripts/windows/run_tms_analytics_export_cleanup.ps1",
        "scripts/windows/run_tms_analytics_export_worker.ps1",
        "scripts/windows/run_tms_api.ps1",
        "scripts/windows/run_tms_cleanup.ps1",
        "scripts/windows/run_tms_formal_cleanup.ps1",
        "scripts/windows/run_tms_worker.ps1",
        "scripts/windows/start_tms_runtime.ps1",
        "scripts/windows/test_tms_migration_readiness.ps1",
        "scripts/windows/test_tms_production_preflight.ps1",
        "scripts/windows/test_tms_runtime_health.ps1",
        "scripts/windows/uninstall_tms_scheduled_tasks.ps1",
    }.issubset(names)
    assert "scripts/windows/run_tms_formal_cleanup.ps1" in names
    assert "scripts/run_formal_artifact_cleanup.py" in names


def test_release_discovery_excludes_untracked_scratch_and_frontend_tests(
    tmp_path: Path,
) -> None:
    root = _minimal_release_source(tmp_path)
    production = root / "frontend" / "src" / "feature.ts"
    production.write_text("export const production = true;\n", encoding="utf-8")
    local_agent_module = root / "local_agent" / "service.py"
    local_agent_module.write_text("AGENT = True\n", encoding="utf-8")
    excluded = (
        root / "backend" / "app" / "scratch.py",
        root / "frontend" / "src" / "scratch.ts",
        root / "frontend" / "src" / "scratch.test.ts",
        root / "frontend" / "src" / "feature.spec.tsx",
        root / "frontend" / "src" / "__tests__" / "fixture.ts",
        root / "frontend" / "src" / "fixtures" / "mock.ts",
        root / "local_agent" / "tests" / "test_app.py",
        root / "local_agent" / "__pycache__" / "cached.py",
        root / "local_agent" / "config.json",
        root / "local_agent" / "pairing-token.txt",
        root / "local_agent" / "work" / "runtime.py",
        root / "local_agent" / "work" / "raw.csv",
        root / "local_agent" / "outputs" / "generated.py",
        root / "local_agent" / "outputs" / "PAT_result.xlsx",
        root / "local_agent" / "logs" / "agent.log",
    )
    for path in excluded:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("export const testOnly = true;\n", encoding="utf-8")

    # The temporary source is not a Git checkout: every allowed-suffix file is
    # effectively untracked, so discovery must rely on the release policy itself.
    names = {
        path.relative_to(root).as_posix()
        for path in release_builder.discover_release_files(root)
    }

    assert "frontend/src/feature.ts" in names
    assert "local_agent/service.py" in names
    assert "local_agent/README.md" in names
    assert "local_agent/config.example.json" in names
    assert not {
        path.relative_to(root).as_posix() for path in excluded
    }.intersection(names)


@pytest.mark.parametrize("target_kind", ["root", "nested-child"])
def test_release_discovery_rejects_reparse_source_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_kind: str
) -> None:
    root = _minimal_release_source(tmp_path)
    if target_kind == "root":
        reparse_target = root
    else:
        reparse_target = root / "frontend" / "src" / "linked-source"
        reparse_target.mkdir(parents=True)
        (reparse_target / "outside.ts").write_text(
            "export const outside = true;\n", encoding="utf-8"
        )
    original = release_builder._is_reparse_point
    monkeypatch.setattr(
        release_builder,
        "_is_reparse_point",
        lambda path: path == reparse_target or original(path),
    )

    with pytest.raises(ReleaseValidationError, match="reparse point"):
        release_builder.discover_release_files(root)


def test_release_runtime_smoke_accepts_only_exact_dev_ready_target() -> None:
    assert _validate_ready_payload(
        {
            "status": "ready",
            "database": "TMS_G0_DEV",
            "schema_revision": "sql2014_0028",
        }
    ) == {
        "status": "ready",
        "database": "TMS_G0_DEV",
        "schema_revision": "sql2014_0028",
    }
    for payload in (
        {
            "status": "starting",
            "database": "TMS_G0_DEV",
            "schema_revision": "sql2014_0028",
        },
        {"status": "ready", "database": "NCE_TMS", "schema_revision": "sql2014_0028"},
        {
            "status": "ready",
            "database": "TMS_G0_DEV",
            "schema_revision": "sql2014_0021",
        },
    ):
        with pytest.raises(ReleaseValidationError, match="readiness target"):
            _validate_ready_payload(payload)


def test_release_runtime_smoke_always_stops_and_revalidates_manifest() -> None:
    source = (ROOT / "scripts" / "release" / "build_tms_release.py").read_text(
        encoding="utf-8-sig"
    )
    smoke = source[
        source.index("def smoke_unpacked_launcher(") : source.index("def main()")
    ]

    assert "finally:\n                    _stop_process_tree(process)" in smoke
    assert (
        "finally:\n            _validate_unpacked_launcher(powershell, launcher)"
        in smoke
    )
    assert '"-ListenAddress"' in smoke
    assert '"127.0.0.1"' in smoke
