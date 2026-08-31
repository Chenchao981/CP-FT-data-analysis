from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.logging import configure_logging
from app.infrastructure.analytics_export_cleanup import AnalyticsExportFileCleaner
from app.infrastructure.analytics_export_files import AnalyticsExportPathPolicy
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.sql_analytics_export_cleanup import (
    SqlAnalyticsExportCleanupService,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect expired Analytics Export Artifacts; pass --delete for "
            "bounded physical removal"
        )
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete exact TMS_ANALYTICS_EXPORT_ROOT/<job_id> roots (DryRun default)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit < 1 or args.limit > 1000:
        raise RuntimeError("limit must be between 1 and 1000")
    if not os.getenv("TMS_DATABASE_URL"):
        raise RuntimeError("TMS_DATABASE_URL is required")
    database = check_database()
    if database["schema_revision"] != "sql2014_0023":
        raise RuntimeError("Analytics Export cleanup requires sql2014_0023")
    configured_root = os.getenv("TMS_ANALYTICS_EXPORT_ROOT", "").strip()
    if not configured_root:
        if os.getenv("TMS_ENV", "").strip().lower() == "production":
            raise RuntimeError("TMS_ANALYTICS_EXPORT_ROOT is required in production")
        configured_root = r"F:\CP-FT数据分析\data\analytics_exports"
    stale_minutes = int(os.getenv("TMS_ANALYTICS_EXPORT_CLEANUP_STALE_MINUTES", "30"))
    if stale_minutes < 1 or stale_minutes > 1440:
        raise RuntimeError(
            "TMS_ANALYTICS_EXPORT_CLEANUP_STALE_MINUTES must be between 1 and 1440"
        )
    policy = AnalyticsExportPathPolicy(Path(configured_root))
    service = SqlAnalyticsExportCleanupService(
        get_engine(),
        AnalyticsExportFileCleaner(policy),
    )
    dry_run = not args.delete
    results = service.run_due(
        limit=args.limit,
        dry_run=dry_run,
        stale_after=timedelta(minutes=stale_minutes),
    )
    payload = {
        "dry_run": dry_run,
        "export_root": str(policy.export_root),
        "result_count": len(results),
        "results": [asdict(item) for item in results],
    }
    configure_logging()
    logging.getLogger(__name__).info(
        "Analytics Export cleanup completed: %s",
        json.dumps(payload, ensure_ascii=False),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
