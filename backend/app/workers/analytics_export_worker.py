from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

from app.core.errors import DomainError
from app.domain.analytics_export_worker import AnalyticsExportWorkerRepository
from app.infrastructure.analytics_export_files import UnsafeAnalyticsExportPath
from app.infrastructure.analytics_export_renderer import AnalyticsExportRenderer

logger = logging.getLogger(__name__)


class AnalyticsExportWorker:
    def __init__(
        self,
        repository: AnalyticsExportWorkerRepository,
        renderer: AnalyticsExportRenderer,
        *,
        heartbeat_seconds: float = 30.0,
    ) -> None:
        if heartbeat_seconds <= 0 or heartbeat_seconds > 300:
            raise ValueError("heartbeat_seconds must be between 0 and 300")
        self._repository = repository
        self._renderer = renderer
        self._heartbeat_seconds = heartbeat_seconds

    def run_once(self):
        work_item = self._repository.claim_next()
        if work_item is None:
            return None
        artifact = None
        completed = False
        try:
            heartbeat = _AnalyticsExportHeartbeat(
                self._repository,
                work_item,
                interval_seconds=self._heartbeat_seconds,
            )
            with heartbeat:
                self._repository.assert_execution_authorized(work_item)
                artifact = self._renderer.render(work_item)
            heartbeat.raise_if_failed()
            expires = datetime.now(UTC) + timedelta(hours=work_item.artifact_ttl_hours)
            self._repository.complete(
                work_item,
                artifact,
                expires_at_utc=expires,
            )
            completed = True
        except DomainError as exc:
            if exc.code == "ANALYTICS_EXPORT_WORKER_CLAIM_LOST":
                pass
            else:
                self._record_failure(
                    work_item,
                    error_code=exc.code,
                    error_message=exc.message,
                )
        except UnsafeAnalyticsExportPath:
            self._record_failure(
                work_item,
                error_code="ANALYTICS_EXPORT_PATH_BLOCKED",
                error_message="managed export path validation failed closed",
            )
        except Exception:  # noqa: BLE001 - do not leak unexpected server details
            self._record_failure(
                work_item,
                error_code="ANALYTICS_EXPORT_RENDER_FAILED",
                error_message="unexpected server rendering failure",
            )
        finally:
            if not completed:
                try:
                    self._renderer.discard_attempt(work_item)
                except (OSError, UnsafeAnalyticsExportPath):
                    logger.exception(
                        "failed to clean rejected Analytics Export attempt",
                        extra={
                            "export_job_id": work_item.export_job_id,
                            "attempt_count": work_item.attempt_count,
                        },
                    )
        return work_item

    def _record_failure(
        self,
        work_item,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        try:
            self._repository.fail(
                work_item,
                error_code=error_code,
                error_message=error_message,
            )
        except DomainError as exc:
            if exc.code != "ANALYTICS_EXPORT_WORKER_CLAIM_LOST":
                raise


class _AnalyticsExportHeartbeat:
    def __init__(
        self,
        repository: AnalyticsExportWorkerRepository,
        work_item,
        *,
        interval_seconds: float,
    ) -> None:
        self._repository = repository
        self._work_item = work_item
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._failure: DomainError | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"analytics-export-heartbeat-{work_item.export_job_id}",
            daemon=True,
        )

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self._stop.set()
        self._thread.join(timeout=max(1.0, self._interval_seconds + 1.0))
        return False

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._repository.heartbeat(self._work_item)
            except DomainError as exc:
                self._failure = exc
                self._stop.set()
            except Exception:  # noqa: BLE001 - stable external failure contract
                self._failure = DomainError(
                    "ANALYTICS_EXPORT_HEARTBEAT_FAILED",
                    "analytics export Worker heartbeat failed",
                    503,
                )
                self._stop.set()

    def raise_if_failed(self) -> None:
        if self._failure is not None:
            raise self._failure
