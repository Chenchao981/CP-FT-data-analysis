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
from app.infrastructure.formal_artifact_files import (
    FormalArtifactFileCleaner,
    FormalOrphanRootCleaner,
    ManagedJobPathPolicy,
)
from app.infrastructure.sql_formal_artifact_cleanup import (
    SqlFormalArtifactCleanupService,
)
from app.infrastructure.sql_formal_orphan_cleanup import (
    SqlFormalOrphanCleanupService,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect expired formal Job artifacts; pass --delete for bounded removal"
        )
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete eligible TMS_WORK_ROOT/<job> directories (default is DryRun)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()
    logger = logging.getLogger(__name__)
    if not os.getenv("TMS_DATABASE_URL"):
        raise RuntimeError("TMS_DATABASE_URL is required")
    work_root = Path(
        os.getenv("TMS_WORK_ROOT", r"F:\CP-FT数据分析\data\work")
    )
    policy = ManagedJobPathPolicy(work_root)
    engine = get_engine()
    service = SqlFormalArtifactCleanupService(
        engine, FormalArtifactFileCleaner(policy)
    )
    stale_minutes = int(os.getenv("TMS_FORMAL_CLEANUP_STALE_MINUTES", "30"))
    if stale_minutes < 1 or stale_minutes > 1440:
        raise RuntimeError(
            "TMS_FORMAL_CLEANUP_STALE_MINUTES must be between 1 and 1440"
        )
    orphan_retention_hours = int(
        os.getenv("TMS_FORMAL_ORPHAN_RETENTION_HOURS", "168")
    )
    if orphan_retention_hours < 1 or orphan_retention_hours > 87600:
        raise RuntimeError(
            "TMS_FORMAL_ORPHAN_RETENTION_HOURS must be between 1 and 87600"
        )
    orphan_max_entries = int(
        os.getenv("TMS_FORMAL_ORPHAN_MAX_ENTRIES", "100000")
    )
    orphan_max_bytes = int(
        os.getenv("TMS_FORMAL_ORPHAN_MAX_BYTES", str(50 * 1024**3))
    )
    orphan_service = SqlFormalOrphanCleanupService(
        engine,
        FormalOrphanRootCleaner(
            policy,
            max_entries=orphan_max_entries,
            max_bytes=orphan_max_bytes,
        ),
    )
    dry_run = not args.delete
    results = service.run_due(
        limit=args.limit,
        dry_run=dry_run,
        stale_after=timedelta(minutes=stale_minutes),
    )
    orphan_results = orphan_service.run(
        limit=args.limit,
        dry_run=dry_run,
        retention=timedelta(hours=orphan_retention_hours),
    )
    payload = {
        "dry_run": dry_run,
        "work_root": str(policy.work_root),
        "result_count": len(results),
        "results": [asdict(item) for item in results],
        "orphan_result_count": len(orphan_results),
        "orphan_results": [asdict(item) for item in orphan_results],
    }
    logger.info(
        "Formal Artifact cleanup completed: %s",
        json.dumps(payload, ensure_ascii=False),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
