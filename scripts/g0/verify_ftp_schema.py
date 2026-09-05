"""Apply/verify additive FTP schema only on the explicitly named development server."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from app.infrastructure.database import check_database, get_engine


def snapshot():
    with get_engine().connect() as connection:
        counts = {name: int(connection.execute(text(f"SELECT COUNT_BIG(*) FROM {name}")).scalar_one())
                  for name in ("test.test_run", "test.cp_die", "test.ft_device", "test.cp_measurement", "test.ft_measurement")}
        versions = [tuple(row) for row in connection.execute(text("SELECT dataset_version_id,dataset_id,version_no,status,is_current FROM dataset.dataset_version ORDER BY dataset_version_id"))]
    return dict(counts=counts, versions_sha256=hashlib.sha256(json.dumps(versions).encode()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-server", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before_identity = check_database()
    if before_identity["database"] != "TMS_G0_DEV" or before_identity["database_server"] != args.expected_server:
        raise RuntimeError("FTP migration requires the explicitly selected development database/server")
    if before_identity["schema_revision"] not in {"sql2014_0028", "sql2014_0029"}:
        raise RuntimeError("Unexpected migration starting revision")
    before = snapshot()
    if args.apply:
        command.upgrade(Config(str(ROOT / "db/alembic/alembic.ini")), "sql2014_0029")
    identity = check_database()
    assert identity["schema_revision"] == "sql2014_0029"
    with get_engine().connect() as connection:
        counts = {name: int(connection.execute(text(f"SELECT COUNT(*) FROM ingestion.{name}")).scalar_one())
                  for name in ("ftp_collection_state", "ftp_collection_run", "ftp_package")}
    after = snapshot()
    assert before == after, "Existing formal facts or Dataset version state changed"
    evidence = dict(verification="PASS", before_identity=before_identity, identity=identity,
                    before=before, after=after, ftp_table_counts=counts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(verification="PASS", revision=identity["schema_revision"], formal_snapshot_unchanged=True)))


if __name__ == "__main__":
    main()
