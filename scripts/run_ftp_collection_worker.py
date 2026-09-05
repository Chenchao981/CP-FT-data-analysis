from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.api.stage_data import _upload_root
from app.core.logging import configure_logging
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.ftp_credentials import read_ftp_credential
from app.infrastructure.sql_ftp_sources import SqlFtpSourceService
from app.workers.ftp_collection_worker import FtpCollectionWorker
from app.workers.runtime_control import is_stop_requested, remove_ready_file, validate_database_identity, write_ready_file


def main():
    parser = argparse.ArgumentParser(description="Collect configured FTP sources into the formal import queue")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--worker-id", default=f"ftp-collector-{uuid4()}")
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--expected-database")
    parser.add_argument("--expected-schema-revision")
    parser.add_argument("--expected-database-server")
    args = parser.parse_args()
    if not 0.1 <= args.poll_seconds <= 60 or len(args.worker_id) > 128:
        parser.error("poll seconds must be 0.1..60 and worker id at most 128 characters")
    remove_ready_file(args.ready_file)
    metadata = check_database()
    validate_database_identity(metadata, expected_database=args.expected_database,
        expected_schema_revision=args.expected_schema_revision, expected_database_server=args.expected_database_server)
    if metadata["schema_revision"] != "sql2014_0029":
        raise RuntimeError("FTP collector requires sql2014_0029")
    configure_logging()
    logger = logging.getLogger("tms.ftp_collection")
    worker = FtpCollectionWorker(SqlFtpSourceService(get_engine()), read_ftp_credential, _upload_root(), args.worker_id,
                                 should_stop=lambda: is_stop_requested(args.stop_file))
    write_ready_file(args.ready_file, args.worker_id, metadata)
    try:
        while not is_stop_requested(args.stop_file):
            result = worker.run_once()
            if result:
                logger.info("FTP scan source_id=%s status=%s submitted=%s", result["source_id"], result["status"], result["submitted"])
            if args.once:
                break
            if result is None:
                time.sleep(args.poll_seconds)
    finally:
        remove_ready_file(args.ready_file)


if __name__ == "__main__":
    main()
