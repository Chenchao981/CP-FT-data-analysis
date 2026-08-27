from __future__ import annotations

import argparse
import logging
import socket
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.logging import configure_logging
from app.domain.jobs import JobType
from app.infrastructure.cp_csv_triplet_writer import CpCsvTripletWriter
from app.infrastructure.database import get_engine
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
    return parser.parse_args()


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
        while True:
            result = worker.run_once()
            if args.once:
                logger.info("Route A Worker one-shot run completed")
                return
            if result is None:
                time.sleep(max(args.poll_seconds, 0.1))
    except KeyboardInterrupt:
        logger.info("Route A Worker stopped by operator")


if __name__ == "__main__":
    main()
