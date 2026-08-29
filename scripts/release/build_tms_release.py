from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

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
    ("scripts/windows", frozenset({".ps1"})),
    ("docs/examples", frozenset({".ps1", ".md"})),
    ("docs/operations", frozenset({".md"})),
    ("frontend/src", frozenset({".css", ".svg", ".ts", ".tsx"})),
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
    "scripts/__init__.py",
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


def discover_release_files(root: Path) -> tuple[Path, ...]:
    discovered: dict[str, Path] = {}
    for relative in sorted(ROOT_FILES | EXPLICIT_FILES):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise ReleaseValidationError(f"required release file is missing: {relative}")
        _safe_relative_path(relative)
        discovered[relative] = path
    for prefix, suffixes in ROOT_PREFIX_RULES:
        directory = root / prefix
        if not directory.is_dir() or directory.is_symlink():
            raise ReleaseValidationError(f"required release directory is missing: {prefix}")
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
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
) -> dict[str, object]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", release_version):
        raise ReleaseValidationError("release version is invalid")
    root = root.resolve()
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
            smoke_unpacked_launcher(output)
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
                raise ReleaseValidationError("release archive must not contain directory entries")
        if archive.testzip() is not None:
            raise ReleaseValidationError("archive CRC inspection failed")
        try:
            manifest = json.loads(archive.read("release-manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ReleaseValidationError("release manifest is missing or invalid") from exc
        if manifest.get("format") != "NCE_TMS_RELEASE_V1":
            raise ReleaseValidationError("release manifest format is unsupported")
        if re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}",
            str(manifest.get("release_version", "")),
        ) is None:
            raise ReleaseValidationError("release manifest version is invalid")
        if re.fullmatch(
            r"sql2014_[0-9]{4}", str(manifest.get("schema_revision", ""))
        ) is None:
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


def smoke_unpacked_launcher(archive_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        raise ReleaseValidationError("powershell.exe is required for launcher smoke")
    with tempfile.TemporaryDirectory(prefix="nce-tms-release-") as temporary:
        target = Path(temporary)
        with zipfile.ZipFile(archive_path, "r") as archive:
            archive.extractall(target)
        launcher = target / "scripts" / "windows" / "start_tms_runtime.ps1"
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
            raise ReleaseValidationError(
                "unpacked launcher smoke failed: " + completed.stderr.strip()
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a reproducible NCE TMS release")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--release-version")
    parser.add_argument("--inspect-only", type=Path)
    parser.add_argument("--skip-launcher-smoke", action="store_true")
    args = parser.parse_args()
    if args.inspect_only is not None:
        manifest = inspect_release_archive(args.inspect_only.resolve())
    else:
        if args.output is None or args.release_version is None:
            parser.error("--output and --release-version are required when building")
        manifest = build_release(
            args.root,
            args.output,
            release_version=args.release_version,
            smoke_launcher=not args.skip_launcher_smoke,
        )
    print(
        json.dumps(
            {
                "status": "VALID",
                "release_version": manifest["release_version"],
                "schema_revision": manifest["schema_revision"],
                "file_count": len(manifest["files"]),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
