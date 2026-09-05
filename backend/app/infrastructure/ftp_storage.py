from __future__ import annotations

import ftplib
import hashlib
import shutil
import ssl
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from app.core.errors import DomainError
from app.domain.ftp_sources import FtpSourceCreate, RemoteFile, RemotePackage, safe_component
from app.domain.stage_data import StoredUpload


@contextmanager
def ftp_connection(config: FtpSourceCreate, credentials):
    username, password = credentials(config.credential_ref)
    ftp = (ftplib.FTP_TLS(context=ssl.create_default_context(), timeout=config.timeout_seconds, encoding=config.encoding)
           if config.protocol == "FTPS" else ftplib.FTP(timeout=config.timeout_seconds, encoding=config.encoding))
    try:
        ftp.connect(config.host, config.port)
        ftp.login(username, password)
        if config.protocol == "FTPS":
            ftp.prot_p()
        ftp.set_pasv(True)
        ftp.cwd(config.remote_root)
        yield ftp
    except DomainError:
        raise
    except (OSError, EOFError, UnicodeError, ftplib.Error):
        # Server responses may contain usernames or endpoint details. Never persist them.
        raise DomainError("FTP_CONNECTION_FAILED", "FTP 连接、证书、权限或传输检查失败，请核对服务器配置", 502) from None
    finally:
        ftp.close()


def read_mlsd(ftp, path: str, *, limit: int):
    # ftplib.mlsd buffers every line before yielding. Bound the wire callback instead.
    entries = []

    def receive(line):
        if len(entries) >= limit:
            raise DomainError("FTP_ENTRY_LIMIT", "FTP 扫描条目超过上限，请缩小根目录范围", 422)
        facts_text, separator, name = line.partition(" ")
        if not separator:
            raise DomainError("FTP_FACTS_REQUIRED", "FTP MLSD 清单格式无效", 422)
        facts = {}
        for fact in facts_text.split(";"):
            if not fact:
                continue
            key, separator, value = fact.partition("=")
            if not separator or key.lower() in facts:
                raise DomainError("FTP_FACTS_REQUIRED", "FTP MLSD 属性不完整或重复", 422)
            facts[key.lower()] = value
        entries.append((name, facts))

    try:
        ftp.retrlines(f"MLSD {path or '.'}", receive)
    except ftplib.error_perm:
        raise DomainError("FTP_MLSD_REQUIRED", "FTP 目录读取需要 MLSD 支持及目录权限", 422) from None
    return entries


def scan_packages(ftp, config: FtpSourceCreate, check_lease=lambda: None, *, selected_path=None) -> tuple[RemotePackage, ...]:
    files: list[RemoteFile] = []
    directories: set[str] = set()
    budget = config.max_entries

    def walk(path: str, depth: int):
        nonlocal budget
        check_lease()
        if depth > 12:
            raise DomainError("FTP_DEPTH_LIMIT", "FTP 目录层级超过扫描上限", 422)
        try:
            entries = read_mlsd(ftp, path, limit=budget)
            names: set[str] = set()
            for name, facts in entries:
                budget -= 1
                if budget < 0:
                    raise DomainError("FTP_ENTRY_LIMIT", "FTP 扫描条目超过上限，请缩小根目录范围", 422)
                kind = facts.get("type", "").lower()
                if kind in {"cdir", "pdir"}:
                    continue
                try:
                    safe_component(name)
                except ValueError:
                    raise DomainError("FTP_PATH_INVALID", "FTP 包含不能安全保存的文件或目录名称", 422) from None
                if name.casefold() in names:
                    raise DomainError("FTP_PATH_COLLISION", "FTP 目录存在 Windows 无法区分的同名条目", 422)
                names.add(name.casefold())
                relative = f"{path}/{name}" if path else name
                if len(relative) > 900:
                    raise DomainError("FTP_PATH_LIMIT", "FTP 相对路径超过长度上限", 422)
                if kind == "dir":
                    directories.add(relative)
                    # Drain MLSD before issuing another command on its control channel.
                elif kind == "file":
                    if PurePosixPath(name).suffix.lower() not in config.allowed_suffixes and name != config.ready_marker:
                        continue
                    try:
                        entry = RemoteFile(relative, int(facts["size"]), facts["modify"])
                        if entry.size < 0:
                            raise ValueError()
                        entry.modified_at
                    except (KeyError, ValueError):
                        raise DomainError("FTP_FACTS_REQUIRED", "FTP 必须提供可靠的文件大小和 UTC 修改时间", 422) from None
                    files.append(entry)
                    if len(files) > config.max_files:
                        raise DomainError("FTP_FILE_LIMIT", "FTP 源文件数量超过扫描上限", 422)
                else:
                    raise DomainError("FTP_LINK_UNSUPPORTED", "FTP 链接及无法识别的条目不允许用于正式采集", 422)
        except ftplib.error_perm:
            raise DomainError("FTP_MLSD_REQUIRED", "FTP 目录读取需要 MLSD 支持及目录权限", 422) from None
        children = sorted(item for item in directories if str(PurePosixPath(item).parent) == (path or "."))
        if selected_path is not None and config.package_mode == "SINGLE_FILE":
            return
        for child in children:
            walk(child, depth + 1)

    selected_root = (str(PurePosixPath(selected_path).parent) if config.package_mode == "SINGLE_FILE" else selected_path) if selected_path else ""
    walk("" if selected_root == "." else selected_root, 0)
    files.sort(key=lambda entry: entry.path)
    data = [entry for entry in files if PurePosixPath(entry.path).suffix.lower() in config.allowed_suffixes]
    if selected_path is not None and config.package_mode == "SINGLE_FILE":
        data = [entry for entry in data if entry.path == selected_path]
    if sum(entry.size for entry in data) > config.max_bytes:
        raise DomainError("FTP_SIZE_LIMIT", "FTP 本次扫描数据量超过配置上限，请缩小采集范围", 422)
    if config.package_mode == "SINGLE_FILE":
        return tuple(RemotePackage(entry.path, (entry,)) for entry in data)
    grouped: dict[str, list[RemoteFile]] = {}
    for entry in data:
        parts = PurePosixPath(entry.path).parts
        if len(parts) <= config.package_depth:
            raise DomainError("FTP_PACKAGE_LAYOUT_INVALID", "源文件不在指定层级的完整批次目录内", 422)
        package = "/".join(parts[:config.package_depth])
        grouped.setdefault(package, []).append(entry)
    indexed = {entry.path: entry for entry in files}
    return tuple(RemotePackage(path, tuple(entries), indexed.get(f"{path}/{config.ready_marker}"),
                               f"{path}/{config.ready_marker}" in indexed)
                 for path, entries in sorted(grouped.items()))


def download_package(ftp, config: FtpSourceCreate, package: RemotePackage, upload_root: Path,
                     *, domain_code: str, check_lease=lambda: None) -> tuple[Path, tuple[StoredUpload, ...], str]:
    upload_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(upload_root).free < package.total_bytes + 1024**3:
        raise DomainError("FTP_DISK_CAPACITY", "采集快照磁盘空间不足", 507)
    target = upload_root / "engineering" / config.test_stage.lower() / uuid4().hex
    target.mkdir(parents=True, exist_ok=False)
    selected = PurePosixPath(package.path)
    base = selected if config.package_mode == "DIRECTORY" else selected.parent
    leaf = base.name or config.source_code
    stored = []
    try:
        for entry in package.files:
            check_lease()
            relative = PurePosixPath(entry.path).relative_to(base)
            if len(str(relative)) > 500:
                raise DomainError("FTP_PATH_LIMIT", "文件在批次内的相对路径超过回执长度上限", 422)
            destination = target / leaf / Path(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with destination.open("xb") as output:
                def consume(chunk):
                    nonlocal size
                    check_lease()
                    size += len(chunk)
                    if size > entry.size:
                        raise DomainError("FTP_SOURCE_CHANGED", "FTP 文件在下载期间发生变化", 409)
                    digest.update(chunk)
                    output.write(chunk)
                ftp.retrbinary(f"RETR {entry.path}", consume, blocksize=1024 * 1024)
            if size != entry.size:
                raise DomainError("FTP_SOURCE_CHANGED", "FTP 文件下载不完整或已发生变化", 409)
            metadata = dict(purpose="FORMAL_IMPORT", source_root_code=config.source_code,
                            data_domain_code=domain_code, source_relative_path=package.path,
                            source_file_relative_path=entry.path, source_manifest_mode="FTP_PATH_SIZE_MTIME_V1",
                            source_manifest_sha256=package.fingerprint, source_file_count=len(package.files),
                            source_total_bytes=package.total_bytes, snapshot_copy=True,
                            snapshot_selected_directory_name=leaf, ftp_protocol=config.protocol)
            stored.append(StoredUpload(str(relative), destination.resolve(), size, digest.hexdigest(), metadata))
        content = hashlib.sha256("\n".join(f"{item.original_name}\t{item.size_bytes}\t{item.sha256}" for item in stored).encode()).hexdigest()
        return target, tuple(stored), content
    except BaseException:
        # Only this newly created UUID directory belongs to the failed attempt.
        shutil.rmtree(target, ignore_errors=True)
        raise
