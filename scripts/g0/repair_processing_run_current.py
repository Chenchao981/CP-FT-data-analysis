from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import Connection, create_engine, text


@dataclass(frozen=True, slots=True)
class LinkedRunState:
    processing_run_id: int
    source_file_id: int | None
    status: str
    is_current: bool
    current_version_ids: tuple[int, ...]
    historical_version_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RunRepair:
    processing_run_id: int
    source_file_id: int | None
    before_status: str
    before_is_current: bool
    target_status: str
    target_is_current: bool
    current_version_ids: tuple[int, ...]
    historical_version_ids: tuple[int, ...]


def choose_run_repair(run: LinkedRunState) -> RunRepair | None:
    """Align one Run to its linked Dataset Versions without comparing Sources."""

    if run.status not in {"PUBLISHED", "SUPERSEDED"}:
        raise ValueError("only Published or Superseded Processing Runs can be repaired")
    target_is_current = bool(run.current_version_ids)
    target_status = "PUBLISHED" if target_is_current else "SUPERSEDED"
    if run.status == target_status and run.is_current == target_is_current:
        return None
    return RunRepair(
        processing_run_id=run.processing_run_id,
        source_file_id=run.source_file_id,
        before_status=run.status,
        before_is_current=run.is_current,
        target_status=target_status,
        target_is_current=target_is_current,
        current_version_ids=run.current_version_ids,
        historical_version_ids=run.historical_version_ids,
    )


def _build_plan(connection: Connection, *, lock: bool) -> tuple[RunRepair, ...]:
    lock_hint = " WITH (UPDLOCK,HOLDLOCK)" if lock else ""
    rows = (
        connection.execute(
            text(
                "SELECT pr.processing_run_id,pr.source_file_id,pr.status,pr.is_current,"
                "dv.dataset_version_id,"
                "CASE WHEN dv.status='PUBLISHED' AND dv.is_current=1 THEN 1 ELSE 0 END "
                "AS version_is_current "
                f"FROM ingestion.processing_run pr{lock_hint} "
                "LEFT JOIN dataset.dataset_version_run dvr "
                "ON dvr.processing_run_id=pr.processing_run_id "
                "LEFT JOIN dataset.dataset_version dv "
                "ON dv.dataset_version_id=dvr.dataset_version_id "
                "WHERE pr.status IN('PUBLISHED','SUPERSEDED') "
                "ORDER BY pr.processing_run_id,dv.dataset_version_id"
            )
        )
        .mappings()
        .all()
    )
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        run_id = int(row["processing_run_id"])
        item = grouped.setdefault(
            run_id,
            {
                "processing_run_id": run_id,
                "source_file_id": (
                    int(row["source_file_id"])
                    if row["source_file_id"] is not None
                    else None
                ),
                "status": str(row["status"]),
                "is_current": bool(row["is_current"]),
                "current_version_ids": set(),
                "historical_version_ids": set(),
            },
        )
        version_id = row["dataset_version_id"]
        if version_id is None:
            continue
        target = (
            item["current_version_ids"]
            if bool(row["version_is_current"])
            else item["historical_version_ids"]
        )
        target.add(int(version_id))

    repairs: list[RunRepair] = []
    for run_id in sorted(grouped):
        item = grouped[run_id]
        state = LinkedRunState(
            processing_run_id=run_id,
            source_file_id=item["source_file_id"],
            status=item["status"],
            is_current=item["is_current"],
            current_version_ids=tuple(sorted(item["current_version_ids"])),
            historical_version_ids=tuple(sorted(item["historical_version_ids"])),
        )
        repair = choose_run_repair(state)
        if repair is not None:
            repairs.append(repair)
    return tuple(repairs)


def _plan_payload(plan: tuple[RunRepair, ...]) -> dict[str, object]:
    return {
        "policy": "DATASET_SCOPED_PROCESSING_RUN_CURRENT_V2",
        "history_preserved": True,
        "repair_count": len(plan),
        "promote_count": sum(item.target_is_current for item in plan),
        "supersede_count": sum(not item.target_is_current for item in plan),
        "repairs": [asdict(item) for item in plan],
    }


def _plan_sha256(plan: tuple[RunRepair, ...]) -> str:
    payload = json.dumps(
        _plan_payload(plan), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run or align Processing Run Current state to linked Current "
            "Dataset Versions while preserving all historical rows"
        )
    )
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-revision", default="sql2014_0028")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-plan-sha256")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    database_url = os.environ.get("TMS_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TMS_DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            identity = (
                connection.execute(
                    text(
                        "SELECT DB_NAME() AS database_name,"
                        "(SELECT version_num FROM alembic_version) AS revision"
                    )
                )
                .mappings()
                .one()
            )
            if (
                str(identity["database_name"]) != args.expected_database
                or str(identity["revision"]) != args.expected_revision
            ):
                raise RuntimeError(f"unexpected database identity: {dict(identity)}")
            plan = _build_plan(connection, lock=False)
        digest = _plan_sha256(plan)
        payload = _plan_payload(plan)
        print(
            "processing_run_current_repair_plan="
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
        print(f"plan_sha256={digest}")
        if not args.execute:
            print("execution=DRY_RUN")
            return
        if args.confirm_plan_sha256 != digest:
            raise RuntimeError(
                "--confirm-plan-sha256 must exactly match the reviewed dry-run plan"
            )

        with engine.begin() as connection:
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
            locked_plan = _build_plan(connection, lock=True)
            if _plan_sha256(locked_plan) != digest:
                raise RuntimeError("repair plan changed after the reviewed dry-run")
            for item in locked_plan:
                updated = connection.execute(
                    text(
                        "UPDATE ingestion.processing_run SET status=:target_status,"
                        "is_current=:target_current "
                        "WHERE processing_run_id=:run AND status=:before_status "
                        "AND is_current=:before_current"
                    ),
                    {
                        "run": item.processing_run_id,
                        "before_status": item.before_status,
                        "before_current": item.before_is_current,
                        "target_status": item.target_status,
                        "target_current": item.target_is_current,
                    },
                )
                if updated.rowcount != 1:
                    raise RuntimeError(
                        f"Processing Run {item.processing_run_id} changed during repair"
                    )
                connection.execute(
                    text(
                        "INSERT governance.audit_log(actor,operation,entity_type,"
                        "entity_id,before_json,after_json,reason,correlation_id) "
                        "VALUES('script:repair_processing_run_current',"
                        "'PROCESSING_RUN_CURRENT_REPAIR','ingestion.processing_run',"
                        ":entity,:before_json,:after_json,:reason,:correlation)"
                    ),
                    {
                        "entity": str(item.processing_run_id),
                        "before_json": json.dumps(
                            {
                                "source_file_id": item.source_file_id,
                                "status": item.before_status,
                                "is_current": item.before_is_current,
                                "current_version_ids": item.current_version_ids,
                                "historical_version_ids": item.historical_version_ids,
                            },
                            separators=(",", ":"),
                        ),
                        "after_json": json.dumps(
                            {
                                "status": item.target_status,
                                "is_current": item.target_is_current,
                                "history_preserved": True,
                            },
                            separators=(",", ":"),
                        ),
                        "reason": (
                            "Align Processing Run Current to linked Current Dataset "
                            "Versions without Source-global deduplication"
                        ),
                        "correlation": f"processing-run-current-repair:{digest}",
                    },
                )

            current_dataset_anomalies = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM dataset.dataset_version dv "
                        "WHERE dv.status='PUBLISHED' AND dv.is_current=1 AND ("
                        "NOT EXISTS(SELECT 1 FROM dataset.dataset_version_run dvr "
                        "WHERE dvr.dataset_version_id=dv.dataset_version_id) OR EXISTS("
                        "SELECT 1 FROM dataset.dataset_version_run dvr "
                        "JOIN ingestion.processing_run pr "
                        "ON pr.processing_run_id=dvr.processing_run_id "
                        "WHERE dvr.dataset_version_id=dv.dataset_version_id "
                        "AND (pr.status<>'PUBLISHED' OR pr.is_current<>1)))"
                    )
                ).scalar_one()
            )
            run_alignment_anomalies = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM ingestion.processing_run pr "
                        "WHERE pr.status IN('PUBLISHED','SUPERSEDED') AND (("
                        "EXISTS(SELECT 1 FROM dataset.dataset_version_run dvr "
                        "JOIN dataset.dataset_version dv "
                        "ON dv.dataset_version_id=dvr.dataset_version_id "
                        "WHERE dvr.processing_run_id=pr.processing_run_id "
                        "AND dv.status='PUBLISHED' AND dv.is_current=1) "
                        "AND (pr.status<>'PUBLISHED' OR pr.is_current<>1)) OR ("
                        "NOT EXISTS(SELECT 1 FROM dataset.dataset_version_run dvr "
                        "JOIN dataset.dataset_version dv "
                        "ON dv.dataset_version_id=dvr.dataset_version_id "
                        "WHERE dvr.processing_run_id=pr.processing_run_id "
                        "AND dv.status='PUBLISHED' AND dv.is_current=1) "
                        "AND (pr.status<>'SUPERSEDED' OR pr.is_current<>0)))"
                    )
                ).scalar_one()
            )
            remaining = current_dataset_anomalies + run_alignment_anomalies
            if remaining:
                raise RuntimeError(
                    f"repair left {remaining} Dataset-scoped Current anomalies"
                )
        print("processing_run_current_repair=PASS history_preserved=true")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
