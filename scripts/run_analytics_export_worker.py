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
    database = check_database()
    if database["schema_revision"] != "sql2014_0024":
        raise RuntimeError("Analytics Export Worker requires sql2014_0024")
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
    while True:
        work_item = worker.run_once()
        if work_item is not None:
            logger.info("Analytics export processed job_id=%s", work_item.export_job_id)
        if args.once:
            return
        if work_item is None:
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
