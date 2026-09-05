from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.logging import configure_logging
from app.infrastructure.analytics_export_files import AnalyticsExportPathPolicy
from app.infrastructure.analytics_export_renderer import AnalyticsExportRenderer
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.sql_analytics_export_content import (
    SqlAnalyticsExportContentSource,
)
from app.infrastructure.sql_analytics_export_worker import (
    SqlAnalyticsExportWorkerRepository,
)
from app.workers.analytics_export_worker import AnalyticsExportWorker
from app.workers.runtime_control import (
    is_stop_requested, remove_ready_file, validate_database_identity, write_ready_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the TMS Analytics Export Worker")
    parser.add_argument(
        "--once", action="store_true", help="process at most one export"
    )
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument(
        "--worker-id", default=f"analytics-export-{socket.gethostname().lower()}"
    )
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--heartbeat-seconds", type=float, default=30.0)
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--expected-database")
    parser.add_argument("--expected-schema-revision")
    parser.add_argument("--expected-database-server")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.poll_seconds <= 0 or args.poll_seconds > 60:
        raise RuntimeError("poll seconds must be between 0 and 60")
    if args.lease_seconds < 30 or args.lease_seconds > 3600:
        raise RuntimeError("lease seconds must be between 30 and 3600")
    if (
        args.heartbeat_seconds <= 0
        or args.heartbeat_seconds >= args.lease_seconds
        or args.heartbeat_seconds > 300
    ):
        raise RuntimeError(
            "heartbeat seconds must be positive, at most 300, and below the lease"
        )
    if not os.getenv("TMS_DATABASE_URL"):
        raise RuntimeError("TMS_DATABASE_URL is required")
    remove_ready_file(args.ready_file)
    database = check_database()
    validate_database_identity(
        database, expected_database=args.expected_database,
        expected_schema_revision=args.expected_schema_revision,
        expected_database_server=args.expected_database_server,
    )
    if database["schema_revision"] != "sql2014_0028":
        raise RuntimeError("Analytics Export Worker requires sql2014_0028")
    configured_root = os.getenv("TMS_ANALYTICS_EXPORT_ROOT", "").strip()
    if not configured_root:
        if os.getenv("TMS_ENV", "").strip().lower() == "production":
            raise RuntimeError("TMS_ANALYTICS_EXPORT_ROOT is required in production")
        configured_root = r"F:\CP-FT数据分析\data\analytics_exports"
    export_root = Path(configured_root)
    policy = AnalyticsExportPathPolicy(export_root)
    engine = get_engine()
    worker = AnalyticsExportWorker(
        SqlAnalyticsExportWorkerRepository(
            engine,
            policy,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
        ),
        AnalyticsExportRenderer(policy, SqlAnalyticsExportContentSource(engine)),
        heartbeat_seconds=args.heartbeat_seconds,
    )
    configure_logging()
    logger = logging.getLogger(__name__)
    logger.info(
        "Analytics Export Worker ready database=%s schema=%s",
        database["database"],
        database["schema_revision"],
    )
    write_ready_file(args.ready_file, args.worker_id, database)
    try:
        run_loop(worker, args, logger)
    finally:
        remove_ready_file(args.ready_file)


def run_loop(worker, args: argparse.Namespace, logger: logging.Logger) -> None:
    while not is_stop_requested(args.stop_file):
        work_item = worker.run_once()
        if work_item is not None:
            logger.info("Analytics export processed job_id=%s", work_item.export_job_id)
        if args.once:
            return
        if work_item is None and not is_stop_requested(args.stop_file):
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
