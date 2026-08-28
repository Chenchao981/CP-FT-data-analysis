from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.logging import configure_logging
from app.domain.jobs import JobType
from app.infrastructure.cp_csv_triplet_writer import CpCsvTripletWriter
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.ft_xlsx_scatter_writer import FtXlsxScatterWriter
from app.infrastructure.source_catalog import SourceCatalog
from app.infrastructure.sql_cleaner_registry import SqlCleanerRegistry
from app.infrastructure.sql_job_service import SqlJobService
from app.infrastructure.sql_quick_analysis_service import SqlQuickAnalysisService
from app.infrastructure.sql_stage_data_service import SqlStageDataService
from app.workers.route_a_worker import (
    DatabaseJobWorker,
    QuickPatHandler,
    RouteAInitialImportHandler,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the TMS Route A SQL queue Worker")
    parser.add_argument("--once", action="store_true", help="process at most one job")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--worker-id", default=f"{socket.gethostname()}-route-a-1")
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
    queue = SqlJobService(engine)
    stage_data = SqlStageDataService(engine)
    registry = SqlCleanerRegistry(engine)
    quick_analysis = SqlQuickAnalysisService(engine)
    handler = RouteAInitialImportHandler(
        registry,
        stage_data,
        CpCsvTripletWriter(engine),
        FtXlsxScatterWriter(engine),
    )
    quick_pat_handler = QuickPatHandler(
        registry,
        quick_analysis,
        SourceCatalog.from_environment(),
    )
    worker = DatabaseJobWorker(
        queue,
        {
            JobType.INITIAL_IMPORT: handler,
            JobType.QUICK_PAT: quick_pat_handler,
        },
        worker_id=args.worker_id,
        lease_for=timedelta(minutes=5),
        heartbeat_every=timedelta(minutes=1),
    )
    try:
        write_ready_file(args.ready_file, args.worker_id, database_metadata)
        while True:
            if is_stop_requested(args.stop_file):
                logger.info("Route A Worker drain completed; stop file detected: %s", args.stop_file)
                return
            result = worker.run_once()
            if args.once:
                logger.info("Route A Worker one-shot run completed")
                return
            if result is None:
                time.sleep(max(args.poll_seconds, 0.1))
    except KeyboardInterrupt:
        logger.info("Route A Worker stopped by operator")
    finally:
        remove_ready_file(args.ready_file)


if __name__ == "__main__":
    main()
