from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sql",
    ".svg",
    ".ts",
    ".tsx",
    ".txt",
}
ROOT_FILES = {
    "README.md",
    "requirements.txt",
}
ROOT_PREFIX_RULES: tuple[tuple[str, frozenset[str]], ...] = (
    ("backend/app", frozenset({".py"})),
    ("db/alembic", frozenset({".py", ".sql"})),
    ("local_agent", frozenset({".py"})),
    ("scripts/windows", frozenset({".ps1"})),
    ("docs/examples", frozenset({".ps1", ".md"})),
    ("docs/operations", frozenset({".md"})),
    ("frontend/src", frozenset({".css", ".svg", ".ts", ".tsx"})),
)
FRONTEND_TEST_FILE_MARKERS = (".test.", ".spec.")
SCRATCH_FILE_PREFIXES = (".scratch.", "scratch.", "scratch_")
FRONTEND_TEST_DIRECTORY_NAMES = frozenset(
    {
        "__fixtures__",
        "__mocks__",
        "__specs__",
        "__tests__",
        "fixtures",
        "mocks",
        "spec",
        "specs",
        "test",
        "test-utils",
        "test_utils",
        "tests",
    }
)
WINDOWS_LOCAL_ACCEPTANCE_FILES = frozenset(
    {
        "scripts/windows/TmsLocalRuntime.Common.ps1",
        "scripts/windows/get_tms_local_test_status.ps1",
        "scripts/windows/start_tms_local_test.ps1",
        "scripts/windows/stop_tms_local_test.ps1",
    }
)
LOCAL_AGENT_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".pytest_cache",
        "__pycache__",
        "artifacts",
        "logs",
        "output",
        "outputs",
        "results",
        "runs",
        "test",
        "tests",
        "work",
        "workspace",
    }
)
EXPLICIT_FILES = {
    "backend/README.md",
    "frontend/README.md",
    "frontend/index.html",
    "frontend/package-lock.json",
    "frontend/package.json",
    "frontend/tsconfig.app.json",
    "frontend/tsconfig.json",
    "frontend/tsconfig.node.json",
    "frontend/vite.config.ts",
    "local_agent/README.md",
    "local_agent/config.example.json",
    "scripts/__init__.py",
    "scripts/run_analytics_export_cleanup.py",
    "scripts/run_analytics_export_worker.py",
    "scripts/run_existing_cleaner.py",
    "scripts/run_formal_artifact_cleanup.py",
    "scripts/run_quick_artifact_cleanup.py",
    "scripts/run_route_a_worker.py",
}
FORBIDDEN_PARTS = {
    ".conda-env",
    ".git",
    ".remember",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "credentials",
    "data",
    "dist",
    "env",
    "evidence",
    "exports",
    "input",
    "inputs",
    "logs",
    "node_modules",
    "output",
    "outputs",
    "quarantine",
    "reports",
    "secrets",
    "uploads",
    "venv",
    "work",
    "workspace",
}
FORBIDDEN_SUFFIXES = {
    ".7z",
    ".bak",
    ".csv",
    ".db",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".tmp",
    ".tsv",
    ".xls",
    ".xlsm",
    ".xlsx",
    ".zip",
}
SECRET_PATTERNS = {
    "PRIVATE_KEY": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS_ACCESS_KEY": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "GITHUB_TOKEN": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "OPENAI_KEY": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "CREDENTIALLED_DATABASE_URL": re.compile(
        rb"(?i)mssql\+pyodbc://[^/@:\s]+:[^@/\s]+@"
    ),
    "LITERAL_RUNTIME_TOKEN": re.compile(
        rb"(?im)^\s*\$env:TMS_(?:JWT_SECRET|HEALTH_BEARER_TOKEN)\s*=\s*"
        rb"['\"][^'\"]+['\"]"
    ),
}


class ReleaseValidationError(RuntimeError):
    pass


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or "\\" in path
        or ":" in candidate.parts[0]
    ):
        raise ReleaseValidationError(f"unsafe release path: {path}")
    lowered_parts = {part.casefold() for part in candidate.parts}
    if lowered_parts & FORBIDDEN_PARTS:
        raise ReleaseValidationError(f"forbidden release path: {path}")
    name = candidate.name.casefold()
    if name == ".env" or name.startswith(".env."):
        raise ReleaseValidationError(f"environment file is forbidden: {path}")
    if candidate.suffix.casefold() in FORBIDDEN_SUFFIXES:
        raise ReleaseValidationError(f"sensitive/generated suffix is forbidden: {path}")
    return candidate


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return path.is_symlink()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _reject_reparse_point(path: Path, *, root: Path) -> None:
    if _is_reparse_point(path):
        try:
            relative = path.relative_to(root).as_posix() or "."
        except ValueError:
            relative = str(path)
        raise ReleaseValidationError(
            f"release source contains a reparse point: {relative}"
        )


def _iter_release_source_files(directory: Path, *, root: Path):
    pending = [directory]
    while pending:
        current = pending.pop()
        children = sorted(current.iterdir(), key=lambda item: item.name.casefold())
        for path in children:
            _reject_reparse_point(path, root=root)
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                yield path


def _is_release_discovery_excluded(relative: str) -> bool:
    if relative in WINDOWS_LOCAL_ACCEPTANCE_FILES:
        return True
    candidate = PurePosixPath(relative)
    name = candidate.name.casefold()
    if name.startswith(SCRATCH_FILE_PREFIXES) or ".scratch." in name:
        return True
    if candidate.parts[:1] == ("local_agent",):
        local_directories = {
            part.casefold() for part in candidate.parts[1:-1]
        }
        if local_directories & LOCAL_AGENT_EXCLUDED_DIRECTORY_NAMES:
            return True
    if candidate.parts[:2] != ("frontend", "src"):
        return False
    frontend_parts = tuple(part.casefold() for part in candidate.parts[2:-1])
    if any(part in FRONTEND_TEST_DIRECTORY_NAMES for part in frontend_parts):
        return True
    return any(marker in name for marker in FRONTEND_TEST_FILE_MARKERS)


def discover_release_files(root: Path) -> tuple[Path, ...]:
    root = Path(os.path.abspath(root))
    if not root.is_dir():
        raise ReleaseValidationError(f"release source root is missing: {root}")
    _reject_reparse_point(root, root=root)
    for child in root.iterdir():
        _reject_reparse_point(child, root=root)
    discovered: dict[str, Path] = {}
    for relative in sorted(ROOT_FILES | EXPLICIT_FILES):
        path = root / relative
        _reject_reparse_point(path, root=root)
        if not path.is_file():
            raise ReleaseValidationError(
                f"required release file is missing: {relative}"
            )
        _safe_relative_path(relative)
        discovered[relative] = path
    for prefix, suffixes in ROOT_PREFIX_RULES:
        directory = root / prefix
        _reject_reparse_point(directory, root=root)
        if not directory.is_dir():
            raise ReleaseValidationError(
                f"required release directory is missing: {prefix}"
            )
        for path in _iter_release_source_files(directory, root=root):
            relative = path.relative_to(root).as_posix()
            if _is_release_discovery_excluded(relative):
                continue
            if path.suffix.casefold() not in suffixes:
                continue
            _safe_relative_path(relative)
            discovered[relative] = path
    if "scripts/windows/start_tms_runtime.ps1" not in discovered:
        raise ReleaseValidationError("release launcher is missing")
    return tuple(discovered[name] for name in sorted(discovered))


def get_schema_head(root: Path) -> str:
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in sorted((root / "db" / "alembic" / "versions").glob("sql2014_*.py")):
        text = path.read_text(encoding="utf-8")
        revision = re.search(
            r'^revision\s*=\s*["\'](sql2014_\d+)["\']\s*$',
            text,
            re.MULTILINE,
        )
        if revision is None:
            raise ReleaseValidationError(f"invalid migration revision: {path.name}")
        revisions.add(revision.group(1))
        parent = re.search(
            r'^down_revision\s*=\s*["\'](sql2014_\d+)["\']\s*$',
            text,
            re.MULTILINE,
        )
        if parent is not None:
            parents.add(parent.group(1))
    heads = sorted(revisions - parents)
    if len(heads) != 1:
        raise ReleaseValidationError(f"expected one schema head, found {heads}")
    return heads[0]


def scan_secrets(relative: str, payload: bytes) -> None:
    if Path(relative).suffix.casefold() not in TEXT_SUFFIXES:
        return
    for code, pattern in SECRET_PATTERNS.items():
        if pattern.search(payload):
            raise ReleaseValidationError(f"secret scan blocked {relative}: {code}")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (0o100644 & 0xFFFF) << 16
    return info


def build_release(
    root: Path,
    output: Path,
    *,
    release_version: str,
    smoke_launcher: bool = True,
    runtime_config: Path | None = None,
    python_path: Path | None = None,
) -> dict[str, object]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", release_version):
        raise ReleaseValidationError("release version is invalid")
    root = Path(os.path.abspath(root))
    output = output.resolve()
    if output.exists():
        raise ReleaseValidationError(f"release output already exists: {output}")
    if output.suffix.casefold() != ".zip":
        raise ReleaseValidationError("release output must use the .zip extension")
    files = discover_release_files(root)
    entries: list[dict[str, object]] = []
    payloads: dict[str, bytes] = {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        scan_secrets(relative, payload)
        payloads[relative] = payload
        entries.append(
            {
                "path": relative,
                "sha256": _sha256_bytes(payload),
                "size": len(payload),
            }
        )
    manifest: dict[str, object] = {
        "format": "NCE_TMS_RELEASE_V1",
        "release_version": release_version,
        "schema_revision": get_schema_head(root),
        "files": entries,
    }
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    all_payloads = {"release-manifest.json": manifest_payload, **payloads}
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(all_payloads):
            archive.writestr(_zip_info(name), all_payloads[name], compresslevel=9)
    try:
        inspect_release_archive(output)
        if smoke_launcher:
            smoke_unpacked_launcher(
                output,
                runtime_config=runtime_config,
                python_path=python_path,
            )
            inspect_release_archive(output)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return manifest


def inspect_release_archive(archive_path: Path) -> dict[str, object]:
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        if names != sorted(names) or len(names) != len(set(names)):
            raise ReleaseValidationError("archive members must be unique and sorted")
        for info in archive.infolist():
            _safe_relative_path(info.filename)
            if info.date_time != ZIP_TIMESTAMP:
                raise ReleaseValidationError("archive timestamp is not reproducible")
            if info.is_dir():
                raise ReleaseValidationError(
                    "release archive must not contain directory entries"
                )
        if archive.testzip() is not None:
            raise ReleaseValidationError("archive CRC inspection failed")
        try:
            manifest = json.loads(archive.read("release-manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ReleaseValidationError(
                "release manifest is missing or invalid"
            ) from exc
        if manifest.get("format") != "NCE_TMS_RELEASE_V1":
            raise ReleaseValidationError("release manifest format is unsupported")
        if (
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}",
                str(manifest.get("release_version", "")),
            )
            is None
        ):
            raise ReleaseValidationError("release manifest version is invalid")
        if (
            re.fullmatch(r"sql2014_[0-9]{4}", str(manifest.get("schema_revision", "")))
            is None
        ):
            raise ReleaseValidationError("release manifest schema revision is invalid")
        manifest_files = manifest.get("files")
        if not isinstance(manifest_files, list) or not manifest_files:
            raise ReleaseValidationError("release manifest files are invalid")
        expected_names = {"release-manifest.json"}
        for item in manifest_files:
            if not isinstance(item, dict):
                raise ReleaseValidationError("release manifest file entry is invalid")
            relative = str(item["path"])
            _safe_relative_path(relative)
            if relative in expected_names:
                raise ReleaseValidationError(f"duplicate manifest path: {relative}")
            if re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))) is None:
                raise ReleaseValidationError(f"release hash is invalid: {relative}")
            if not isinstance(item.get("size"), int) or int(item["size"]) < 0:
                raise ReleaseValidationError(f"release size is invalid: {relative}")
            payload = archive.read(relative)
            scan_secrets(relative, payload)
            if len(payload) != int(item["size"]):
                raise ReleaseValidationError(f"release size mismatch: {relative}")
            if _sha256_bytes(payload) != str(item["sha256"]):
                raise ReleaseValidationError(f"release hash mismatch: {relative}")
            expected_names.add(relative)
        if set(names) != expected_names:
            raise ReleaseValidationError("archive contains files outside the manifest")
        return manifest


def _validate_unpacked_launcher(powershell: str, launcher: Path) -> None:
    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(launcher),
            "-ValidateOnly",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0 or "RELEASE_VALID" not in completed.stdout:
        raise ReleaseValidationError("unpacked launcher manifest validation failed")


def _validate_unpacked_local_agent(python_path: Path, target: Path) -> None:
    config = target / "local_agent" / "config.example.json"
    if not config.is_file():
        raise ReleaseValidationError("unpacked Local Agent example config is missing")
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            [
                str(python_path),
                "-m",
                "local_agent",
                "--config",
                str(config),
                "--validate-only",
            ],
            cwd=target,
            env=environment,
            stdin=subprocess.DEVNULL,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseValidationError(
            "unpacked Local Agent validation could not run"
        ) from exc
    if completed.returncode != 0:
        raise ReleaseValidationError("unpacked Local Agent validation failed")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseValidationError(
            "unpacked Local Agent validation returned invalid JSON"
        ) from exc
    tools = payload.get("tools") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("valid") is not True
        or payload.get("bind_host") != "127.0.0.1"
        or not isinstance(tools, list)
        or not tools
    ):
        raise ReleaseValidationError(
            "unpacked Local Agent validation contract is invalid"
        )


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _validate_ready_payload(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ReleaseValidationError("unpacked API readiness payload is invalid")
    if (
        payload.get("status") != "ready"
        or payload.get("database") != "TMS_G0_DEV"
        or payload.get("schema_revision") != "sql2014_0028"
    ):
        raise ReleaseValidationError("unpacked API readiness target is invalid")
    return {
        "status": "ready",
        "database": "TMS_G0_DEV",
        "schema_revision": "sql2014_0028",
    }


def _wait_for_unpacked_api(
    process: subprocess.Popen[bytes],
    *,
    port: int,
    timeout_seconds: float,
) -> dict[str, str]:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/api/v1/health/ready"
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise ReleaseValidationError(
                f"unpacked API exited before readiness (code {return_code})"
            )
        try:
            with urlopen(url, timeout=2.0) as response:
                if response.status == 200:
                    return _validate_ready_payload(json.load(response))
        except HTTPError as exc:
            if exc.code == 200:
                raise
        except (URLError, TimeoutError, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    raise ReleaseValidationError("unpacked API readiness timed out")


def _stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = shutil.which("taskkill.exe")
        if taskkill is not None:
            subprocess.run(
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def smoke_unpacked_launcher(
    archive_path: Path,
    *,
    runtime_config: Path | None = None,
    python_path: Path | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, str] | None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        raise ReleaseValidationError("powershell.exe is required for launcher smoke")
    if (runtime_config is None) != (python_path is None):
        raise ReleaseValidationError(
            "runtime_config and python_path must be supplied together"
        )
    with tempfile.TemporaryDirectory(prefix="nce-tms-release-") as temporary:
        target = Path(temporary)
        with zipfile.ZipFile(archive_path, "r") as archive:
            archive.extractall(target)
        launcher = target / "scripts" / "windows" / "start_tms_runtime.ps1"
        _validate_unpacked_launcher(powershell, launcher)
        local_agent_python = (
            python_path.resolve(strict=True)
            if python_path is not None
            else Path(sys.executable).resolve(strict=True)
        )
        _validate_unpacked_local_agent(local_agent_python, target)
        if runtime_config is None or python_path is None:
            return None
        runtime_config = runtime_config.resolve(strict=True)
        python_path = python_path.resolve(strict=True)
        if not runtime_config.is_file() or not python_path.is_file():
            raise ReleaseValidationError("external runtime files are missing")
        evidence: dict[str, str]
        try:
            with tempfile.TemporaryDirectory(prefix="nce-tms-runtime-") as runtime:
                runtime_home = Path(runtime)
                port = _reserve_loopback_port()
                environment = os.environ.copy()
                environment["PYTHONDONTWRITEBYTECODE"] = "1"
                environment["PYTHONIOENCODING"] = "utf-8"
                process = subprocess.Popen(
                    [
                        powershell,
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(launcher),
                        "-Role",
                        "API",
                        "-RuntimeHome",
                        str(runtime_home),
                        "-RuntimeConfigPath",
                        str(runtime_config),
                        "-PythonPath",
                        str(python_path),
                        "-ListenAddress",
                        "127.0.0.1",
                        "-Port",
                        str(port),
                    ],
                    cwd=target,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    evidence = _wait_for_unpacked_api(
                        process,
                        port=port,
                        timeout_seconds=timeout_seconds,
                    )
                finally:
                    _stop_process_tree(process)
        finally:
            _validate_unpacked_launcher(powershell, launcher)
        return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a reproducible NCE TMS release")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--release-version")
    parser.add_argument("--inspect-only", type=Path)
    parser.add_argument("--skip-launcher-smoke", action="store_true")
    parser.add_argument("--runtime-config", type=Path)
    parser.add_argument("--python-path", type=Path)
    args = parser.parse_args()
    runtime_smoke = "NOT_RUN"
    if args.inspect_only is not None:
        manifest = inspect_release_archive(args.inspect_only.resolve())
    else:
        if args.output is None or args.release_version is None:
            parser.error("--output and --release-version are required when building")
        root = Path(os.path.abspath(args.root))
        runtime_config = None
        python_path = None
        if not args.skip_launcher_smoke:
            runtime_config = (
                args.runtime_config or root / ".env.runtime.ps1"
            ).resolve()
            python_path = (
                args.python_path or root / ".conda-env" / "python.exe"
            ).resolve()
        manifest = build_release(
            root,
            args.output,
            release_version=args.release_version,
            smoke_launcher=not args.skip_launcher_smoke,
            runtime_config=runtime_config,
            python_path=python_path,
        )
        runtime_smoke = "SKIPPED" if args.skip_launcher_smoke else "PASS"
    print(
        json.dumps(
            {
                "status": "VALID",
                "release_version": manifest["release_version"],
                "schema_revision": manifest["schema_revision"],
                "file_count": len(manifest["files"]),
                "runtime_smoke": runtime_smoke,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
