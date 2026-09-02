from __future__ import annotations

import ftplib
import hashlib
import json
from dataclasses import asdict, dataclass
from posixpath import join as posix_join
from urllib.parse import urlparse

from app.core.errors import DomainError


@dataclass(frozen=True, slots=True)
class FtpManifestFile:
    relative_path: str
    size_bytes: int
    modified: str | None


@dataclass(frozen=True, slots=True)
class FtpManifestPreview:
    protocol: str
    server: str
    port: int
    remote_path: str
    files: tuple[FtpManifestFile, ...]
    total_bytes: int
    sha256: str

    @property
    def file_count(self) -> int:
        return len(self.files)


def preview_ftp_directory(
    *,
    protocol: str,
    server: str,
    port: int | None,
    username: str,
    password: str,
    remote_path: str,
    max_files: int = 100_000,
    timeout_seconds: float = 15.0,
) -> FtpManifestPreview:
    normalized_protocol = protocol.strip().upper()
    host = _host(server)
    selected_port = port or 21
    ftp_class = ftplib.FTP_TLS if normalized_protocol == "FTPS" else ftplib.FTP
    ftp = ftp_class(timeout=timeout_seconds)
    try:
        ftp.connect(host, selected_port)
        ftp.login(username, password)
        if isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()
        ftp.cwd(remote_path)
        selected_root = ftp.pwd()
        files: list[FtpManifestFile] = []
        _walk_mlsd(ftp, selected_root, "", files, max_files=max_files)
    except DomainError:
        raise
    except ftplib.all_errors + (OSError, UnicodeError) as exc:
        raise DomainError(
            "TEMP_FTP_UNAVAILABLE",
            "FTP 连接或目录读取失败，请检查地址、端口、账号密码和目录",
            422,
        ) from exc
    finally:
        try:
            ftp.quit()
        except ftplib.all_errors + (OSError,):
            ftp.close()
    files.sort(key=lambda item: item.relative_path.casefold())
    if not files:
        raise DomainError(
            "TEMP_FTP_EMPTY", "FTP 目录中没有可分析的 CSV 文件", 422
        )
    payload = {
        "mode": "FTP_PATH_SIZE_MTIME_V1",
        "protocol": normalized_protocol,
        "server": host,
        "port": selected_port,
        "remote_path": selected_root,
        "files": [asdict(item) for item in files],
        "file_count": len(files),
        "total_bytes": sum(item.size_bytes for item in files),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return FtpManifestPreview(
        normalized_protocol,
        host,
        selected_port,
        selected_root,
        tuple(files),
        int(payload["total_bytes"]),
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _walk_mlsd(
    ftp: ftplib.FTP,
    absolute_path: str,
    relative_prefix: str,
    files: list[FtpManifestFile],
    *,
    max_files: int,
) -> None:
    try:
        entries = list(ftp.mlsd(absolute_path, facts=["type", "size", "modify"]))
    except ftplib.error_perm as exc:
        raise DomainError(
            "TEMP_FTP_MLSD_REQUIRED",
            "该 FTP 服务器不支持目录预览所需的 MLSD 命令，请改为后台配置数据源",
            422,
        ) from exc
    for name, facts in entries:
        if name in {".", ".."}:
            continue
        entry_type = facts.get("type", "").lower()
        relative = posix_join(relative_prefix, name) if relative_prefix else name
        absolute = posix_join(absolute_path.rstrip("/"), name)
        if entry_type == "dir":
            _walk_mlsd(
                ftp,
                absolute,
                relative,
                files,
                max_files=max_files,
            )
        elif entry_type == "file" and name.lower().endswith(".csv"):
            if len(files) >= max_files:
                raise DomainError(
                    "TEMP_FTP_FILE_LIMIT",
                    f"FTP 目录文件数超过预览上限 {max_files}",
                    422,
                )
            files.append(
                FtpManifestFile(relative, int(facts.get("size", "0")), facts.get("modify"))
            )


def _host(server: str) -> str:
    raw = server.strip()
    parsed = urlparse(raw if "://" in raw else f"ftp://{raw}")
    if not parsed.hostname:
        raise DomainError("TEMP_FTP_ADDRESS_INVALID", "FTP 服务器地址无效", 422)
    return parsed.hostname
