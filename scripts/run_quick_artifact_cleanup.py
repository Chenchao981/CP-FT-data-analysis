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
from app.infrastructure.database import get_engine
from app.infrastructure.quick_artifact_cleanup import QuickArtifactFileCleaner
from app.infrastructure.sql_quick_cleanup_service import SqlQuickCleanupService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean expired Quick Analysis artifacts under the controlled work root"
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    logger = logging.getLogger(__name__)
    if not os.getenv("TMS_DATABASE_URL"):
        raise RuntimeError("TMS_DATABASE_URL is required")
    work_root = Path(
        os.getenv("TMS_QUICK_WORK_ROOT", r"F:\CP-FT数据分析\data\workspace")
    ).resolve()
    service = SqlQuickCleanupService(
        get_engine(), QuickArtifactFileCleaner(work_root)
    )
    stale_minutes = int(os.getenv("TMS_QUICK_CLEANUP_STALE_MINUTES", "30"))
    if stale_minutes < 1 or stale_minutes > 1440:
        raise RuntimeError(
            "TMS_QUICK_CLEANUP_STALE_MINUTES must be between 1 and 1440"
        )
    results = service.run_due(
        limit=args.limit,
        dry_run=args.dry_run,
        stale_after=timedelta(minutes=stale_minutes),
    )
    payload = {
        "dry_run": args.dry_run,
        "work_root": str(work_root),
        "result_count": len(results),
        "results": [asdict(item) for item in results],
    }
    logger.info(
        "Quick Artifact cleanup completed: %s",
        json.dumps(payload, ensure_ascii=False),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
