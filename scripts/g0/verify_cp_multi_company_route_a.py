from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api.dependencies import current_principal
from app.domain.jobs import JobType
from app.infrastructure.cp_csv_triplet_writer import CpCsvTripletWriter
from app.infrastructure.database import get_engine
from app.infrastructure.ft_xlsx_scatter_writer import FtXlsxScatterWriter
from app.infrastructure.source_catalog import SourceCatalog, SourceRoot
from app.infrastructure.sql_auth_service import SqlAuthService
from app.infrastructure.sql_cleaner_registry import SqlCleanerRegistry
from app.infrastructure.sql_job_service import SqlJobService
from app.infrastructure.sql_stage_data_service import SqlStageDataService
from app.main import create_app
from app.workers.route_a_worker import (
    DatabaseJobWorker,
    RouteAInitialImportHandler,
)
from fastapi.testclient import TestClient
from sqlalchemy import text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload and process one real CP source through Route A"
    )
    parser.add_argument("--factory", required=True, choices=("huahong", "jetech", "lion"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--domain", default="engineering", choices=("engineering", "production"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.source.resolve()
    if not source.is_dir():
        raise NotADirectoryError(source)

    engine = get_engine()
    with engine.connect() as connection:
        user_id = connection.execute(
            text(
                "SELECT TOP(1) u.user_id FROM iam.app_user u "
                "JOIN iam.user_role ur ON ur.user_id=u.user_id "
                "JOIN iam.role r ON r.role_id=ur.role_id "
                "WHERE u.status='ACTIVE' AND r.role_code='SYSTEM_ADMIN' "
                "ORDER BY u.user_id"
            )
        ).scalar_one()
    principal = SqlAuthService(engine).principal_for_user(int(user_id))
    app = create_app()
    factory_code = {
        "huahong": "HUAHONG",
        "jetech": "JETECH",
        "lion": "LION",
    }[args.factory]
    suffixes = {
        "huahong": (".zip", ".7z", ".txt"),
        "jetech": (".zip", ".xls", ".xlsx"),
        "lion": (".zip", ".xls", ".xlsx"),
    }[args.factory]
    app.state.source_catalog = SourceCatalog(
        (
            SourceRoot(
                "G0_CP_SOURCE",
                "G0 CP verification source",
                source,
                "CP",
                factory_code,
                suffixes,
                "FORMAL_IMPORT",
                (args.domain.upper(),),
            ),
        )
    )
    app.dependency_overrides[current_principal] = lambda: principal
    with TestClient(app) as client:
        preview_response = client.get(
            f"/api/v1/{args.domain}/cp/source-roots/G0_CP_SOURCE/manifest-preview",
            params={"factory_code": args.factory, "relative_path": "."},
        )
        preview_response.raise_for_status()
        preview = preview_response.json()
        response = client.post(
            f"/api/v1/{args.domain}/cp/uploads",
            data={
                "factory_code": args.factory,
                "source_root_code": "G0_CP_SOURCE",
                "source_relative_path": ".",
                "source_manifest_mode": preview["mode"],
                "source_manifest_sha256": preview["sha"],
                "remark": "CP multi-company Route A verification",
            },
        )
        response.raise_for_status()
        receipt = response.json()

        queue = SqlJobService(engine)
        worker = DatabaseJobWorker(
            queue,
            {
                JobType.INITIAL_IMPORT: RouteAInitialImportHandler(
                    SqlCleanerRegistry(engine),
                    SqlStageDataService(engine),
                    CpCsvTripletWriter(engine),
                    FtXlsxScatterWriter(engine),
                    finalizer=queue,
                )
            },
            worker_id=f"{socket.gethostname()}-cp-multi-company-verification",
            lease_for=timedelta(minutes=5),
            heartbeat_every=timedelta(minutes=1),
        )
        target_job_id = int(receipt["job_id"])
        finished = None
        for _ in range(100):
            candidate = worker.run_once()
            if candidate is None:
                break
            if candidate.job_id == target_job_id:
                finished = candidate
                break
        if finished is None:
            raise RuntimeError(f"verification job {target_job_id} was not consumed")
        if finished.status.value != "SUCCESS":
            raise RuntimeError(
                f"verification job {target_job_id} failed: {finished.error_message}"
            )

        results = client.get(f"/api/v1/{args.domain}/cp/results")
        results.raise_for_status()
        row = next(
            item
            for item in results.json()
            if int(item["import_batch_id"]) == int(receipt["import_batch_id"])
        )
        print(
            json.dumps(
                {
                    "factory": args.factory,
                    "import_batch_id": receipt["import_batch_id"],
                    "job_id": target_job_id,
                    "dataset_id": row["dataset_id"],
                    "dataset_version_no": row["dataset_version_no"],
                    "product_name": row["product_name"],
                    "lot_id": row["lot_id"],
                    "wafer_count": row["wafer_count"],
                    "unit_count": row["unit_count"],
                    "pass_count": row["pass_count"],
                    "test_item_count": row["test_item_count"],
                    "status": row["status"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
