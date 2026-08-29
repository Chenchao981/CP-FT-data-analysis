from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.logging import configure_logging
from app.domain.jobs import JobType
from app.domain.worker_operations import WorkerControlState, WorkerOperationsService
from app.infrastructure.cp_csv_triplet_writer import CpCsvTripletWriter
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.formal_artifact_files import ManagedJobPathPolicy
from app.infrastructure.ft_xlsx_scatter_writer import FtXlsxScatterWriter
from app.infrastructure.source_catalog import SourceCatalog
from app.infrastructure.sql_cleaner_registry import SqlCleanerRegistry
from app.infrastructure.sql_job_service import SqlJobService
from app.infrastructure.sql_lifecycle_service import SqlLifecycleService
from app.infrastructure.sql_quick_analysis_service import SqlQuickAnalysisService
from app.infrastructure.sql_stage_data_service import SqlStageDataService
from app.infrastructure.sql_worker_operations_service import (
    SqlWorkerOperationsService,
)
from app.workers.lifecycle_worker import (
    ExportLatestHandler,
    LogicalArchiveHandler,
)
from app.workers.route_a_worker import (
    DatabaseJobWorker,
    QuickPatHandler,
    RouteAInitialImportHandler,
)


def worker_host_fingerprint() -> str:
    host = socket.gethostname().strip().lower()
    if not host:
        raise RuntimeError("Worker host identity is unavailable")
    return hashlib.sha256(host.encode("utf-8")).hexdigest()


def default_worker_id() -> str:
    return f"route-a-{worker_host_fingerprint()[:16]}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the TMS Route A SQL queue Worker")
    parser.add_argument("--once", action="store_true", help="process at most one job")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--worker-id", default=default_worker_id())
    parser.add_argument(
        "--registry-heartbeat-seconds",
        type=float,
        default=15.0,
        help="Worker registry heartbeat interval while a job is running",
    )
    parser.add_argument(
        "--stop-file",
        type=Path,
        help="finish the current job and exit when this local control file exists",
    )
    parser.add_argument(
        "--ready-file",
        type=Path,
        help="write local Worker readiness metadata after the SQL connection succeeds",
    )
    parser.add_argument("--expected-database")
    parser.add_argument("--expected-schema-revision")
    parser.add_argument("--expected-database-server")
    return parser.parse_args()


def is_stop_requested(stop_file: Path | None) -> bool:
    return stop_file is not None and stop_file.is_file()


def write_ready_file(
    ready_file: Path | None,
    worker_id: str,
    database_metadata: dict[str, str],
) -> None:
    if ready_file is None:
        return
    ready_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = ready_file.with_name(f"{ready_file.name}.{os.getpid()}.tmp")
    payload = {
        "status": "READY",
        "pid": os.getpid(),
        "worker_id": worker_id,
        "ready_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": database_metadata["database"],
        "schema_revision": database_metadata["schema_revision"],
        "database_server": database_metadata["database_server"],
    }
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, ready_file)


def remove_ready_file(ready_file: Path | None) -> None:
    if ready_file is not None:
        ready_file.unlink(missing_ok=True)


def validate_database_identity(
    database_metadata: dict[str, str],
    *,
    expected_database: str | None,
    expected_schema_revision: str | None,
    expected_database_server: str | None,
) -> None:
    expected = {
        "database": expected_database,
        "schema_revision": expected_schema_revision,
        "database_server": expected_database_server,
    }
    supplied = [value is not None for value in expected.values()]
    if any(supplied) and not all(supplied):
        raise RuntimeError("all expected database identity fields must be supplied together")
    mismatches = [
        field
        for field, value in expected.items()
        if value is not None and database_metadata.get(field) != value
    ]
    if mismatches:
        actual = "/".join(database_metadata.get(field, "") for field in expected)
        wanted = "/".join(value or "" for value in expected.values())
        raise RuntimeError(
            f"Worker database identity rejected {actual}; expected {wanted}"
        )


class WorkerRegistrationMonitor:
    def __init__(
        self,
        service: WorkerOperationsService,
        worker_id: str,
        *,
        heartbeat_every: float,
        initial: WorkerControlState,
    ) -> None:
        if heartbeat_every <= 0:
            raise ValueError("registry heartbeat interval must be positive")
        self._service = service
        self._worker_id = worker_id
        self._heartbeat_every = heartbeat_every
        self._stop = threading.Event()
        self._drain = threading.Event()
        self._errors: list[Exception] = []
        self._thread: threading.Thread | None = None
        self._apply_control(initial)

    @property
    def drain_requested(self) -> bool:
        return self._drain.is_set()

    def _apply_control(self, control: WorkerControlState) -> None:
        if control.desired_state == "DRAIN":
            self._drain.set()
        else:
            self._drain.clear()

    def refresh(self) -> WorkerControlState:
        control = self._service.heartbeat(self._worker_id)
        self._apply_control(control)
        return control

    def raise_if_failed(self) -> None:
        if self._errors:
            raise RuntimeError("Worker registry heartbeat failed")

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Worker registry heartbeat already started")

        def keep_alive() -> None:
            while not self._stop.wait(self._heartbeat_every):
                try:
                    self.refresh()
                except Exception as exc:  # noqa: BLE001 - surfaced on the main thread
                    self._errors.append(exc)
                    self._drain.set()
                    return

        self._thread = threading.Thread(
            target=keep_alive,
            name=f"tms-worker-registry-{self._worker_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


def run_worker_loop(
    worker: DatabaseJobWorker,
    monitor: WorkerRegistrationMonitor,
    *,
    once: bool,
    poll_seconds: float,
    stop_file: Path | None,
    logger: logging.Logger,
    sleep=time.sleep,
) -> str:
    while True:
        monitor.raise_if_failed()
        if is_stop_requested(stop_file):
            logger.info(
                "Route A Worker local drain completed; stop file detected: %s",
                stop_file,
            )
            return "LOCAL_STOP"
        control = monitor.refresh()
        if control.desired_state == "DRAIN" or monitor.drain_requested:
            logger.info(
                "Route A Worker database drain completed; process exits cleanly and "
                "resume permits the next start"
            )
            return "DATABASE_DRAIN"
        result = worker.run_once()
        monitor.raise_if_failed()
        if monitor.drain_requested:
            logger.info(
                "Route A Worker current job finished after a database drain request; "
                "process exits cleanly and resume permits the next start"
            )
            return "DATABASE_DRAIN"
        if once:
            logger.info("Route A Worker one-shot run completed")
            return "ONCE"
        if result is None:
            sleep(max(poll_seconds, 0.1))


def main() -> None:
    args = parse_args()
    configure_logging()
    logger = logging.getLogger(__name__)
    logger.info(
        "Route A Worker starting: worker_id=%s poll_seconds=%s once=%s",
        args.worker_id,
        args.poll_seconds,
        args.once,
    )
    engine = get_engine()
    database_metadata = check_database()
    validate_database_identity(
        database_metadata,
        expected_database=args.expected_database,
        expected_schema_revision=args.expected_schema_revision,
        expected_database_server=args.expected_database_server,
    )
    if args.registry_heartbeat_seconds < 1:
        raise RuntimeError("registry heartbeat interval must be at least one second")
    queue = SqlJobService(engine)
    stage_data = SqlStageDataService(engine)
    registry = SqlCleanerRegistry(engine)
    quick_analysis = SqlQuickAnalysisService(engine)
    handler = RouteAInitialImportHandler(
        registry,
        stage_data,
        CpCsvTripletWriter(engine),
        FtXlsxScatterWriter(engine),
        finalizer=queue,
    )
    quick_pat_handler = QuickPatHandler(
        registry,
        quick_analysis,
        SourceCatalog.from_environment(),
    )
    lifecycle_paths = ManagedJobPathPolicy(
        Path(os.getenv("TMS_WORK_ROOT", r"F:\CP-FT数据分析\data\work"))
    )
    lifecycle = SqlLifecycleService(engine, lifecycle_paths)
    worker = DatabaseJobWorker(
        queue,
        {
            JobType.INITIAL_IMPORT: handler,
            JobType.QUICK_PAT: quick_pat_handler,
            JobType.EXPORT_LATEST: ExportLatestHandler(
                registry, lifecycle, lifecycle_paths
            ),
            JobType.DELETE_TASK: LogicalArchiveHandler(lifecycle),
        },
        worker_id=args.worker_id,
        lease_for=timedelta(minutes=5),
        heartbeat_every=timedelta(minutes=1),
    )
    operations = SqlWorkerOperationsService(engine)
    registration = operations.register(
        worker_id=args.worker_id,
        worker_kind="ROUTE_A",
        database_name=database_metadata["database"],
        schema_revision=database_metadata["schema_revision"],
        host_fingerprint=worker_host_fingerprint(),
    )
    monitor = WorkerRegistrationMonitor(
        operations,
        args.worker_id,
        heartbeat_every=args.registry_heartbeat_seconds,
        initial=registration,
    )
    failed = False
    try:
        monitor.start()
        if registration.desired_state == "RUN":
            write_ready_file(args.ready_file, args.worker_id, database_metadata)
        run_worker_loop(
            worker,
            monitor,
            once=args.once,
            poll_seconds=args.poll_seconds,
            stop_file=args.stop_file,
            logger=logger,
        )
    except KeyboardInterrupt:
        logger.info("Route A Worker stopped by operator")
    except Exception:
        failed = True
        raise
    finally:
        monitor.stop()
        remove_ready_file(args.ready_file)
        try:
            operations.mark_stopped(args.worker_id, failed=failed)
        except Exception:
            logger.exception("Route A Worker terminal registry update failed")
            if not failed:
                raise


if __name__ == "__main__":
    main()
