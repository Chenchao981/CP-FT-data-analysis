from __future__ import annotations

import shutil
import threading
from pathlib import Path

from app.core.errors import DomainError
from app.infrastructure.ftp_storage import download_package, ftp_connection, scan_packages


class FtpCollectionWorker:
    def __init__(self, repository, credentials, upload_root: Path, worker_id: str, should_stop=lambda: False):
        self.repository = repository
        self.credentials = credentials
        self.upload_root = upload_root
        self.worker_id = worker_id
        self.should_stop = should_stop

    def run_once(self):
        if self.should_stop():
            return None
        claimed = self.repository.claim(self.worker_id)
        if claimed is None:
            return None
        source_id, token, config = claimed["source_id"], claimed["token"], claimed["config"]
        stop = threading.Event()
        lost = threading.Event()

        def heartbeat():
            while not stop.wait(20):
                try:
                    if not self.repository.heartbeat(source_id, token):
                        lost.set()
                        return
                except Exception:
                    lost.set()
                    return

        def check_lease():
            if lost.is_set():
                raise DomainError("FTP_LEASE_LOST", "采集执行权已失效，本次快照未提交", 409)

        monitor = threading.Thread(target=heartbeat, daemon=True, name="ftp-collection-lease")
        monitor.start()
        discovered = submitted = 0
        failure = None
        try:
            binding = self.repository.validate_claim(source_id, token, config)
            with ftp_connection(config, self.credentials) as ftp:
                packages = scan_packages(ftp, config, check_lease)
                discovered = len(packages)
                attempted = 0
                for package in packages:
                    if self.should_stop():
                        break
                    check_lease()
                    if not self.repository.observe(source_id, token, package, config):
                        continue
                    if attempted >= config.max_packages_per_scan:
                        continue
                    attempted += 1
                    target = None
                    registration_started = False
                    try:
                        target, files, sha = download_package(ftp, config, package, self.upload_root,
                            domain_code=binding["domain_code"], check_lease=check_lease)
                        # Check the whole package, including completion marker, again before registration.
                        after = {item.key: item for item in scan_packages(ftp, config, check_lease, selected_path=package.path)}.get(package.key)
                        if after is None or after.fingerprint != package.fingerprint:
                            raise DomainError("FTP_SOURCE_CHANGED", "下载期间远端批次清单变化，未创建入库任务", 409)
                        check_lease()
                        registration_started = True
                        self.repository.submit(source_id, token, package, files, sha)
                        submitted += 1
                    except Exception as exc:
                        # A lost commit response is ambiguous. Retain its snapshot; the atomic
                        # SQL checkpoint resolves the outcome on the next scan without duplication.
                        if target is not None and not registration_started:
                            resolved = target.resolve()
                            scope = (self.upload_root / "engineering" / config.test_stage.lower()).resolve()
                            if resolved.parent == scope and len(resolved.name) == 32:
                                shutil.rmtree(resolved, ignore_errors=True)
                        error = exc if isinstance(exc, DomainError) else DomainError("FTP_COLLECTION_FAILED", "采集未完成，已保留检查点；请查看运行配置后重试", 503)
                        self.repository.package_failed(source_id, token, package, error)
                        failure = error
                        # A failed transfer can desynchronize the FTP control channel.
                        break
        except Exception as exc:
            failure = exc if isinstance(exc, DomainError) else DomainError("FTP_COLLECTION_FAILED", "FTP 采集未完成，请核对运行配置和连接状态", 503)
        finally:
            stop.set()
            monitor.join(timeout=5)
        self.repository.finish(source_id, token, config, discovered=discovered, submitted=submitted, error=failure)
        return dict(source_id=source_id, discovered=discovered, submitted=submitted, status="FAILED" if failure else "SUCCESS")
