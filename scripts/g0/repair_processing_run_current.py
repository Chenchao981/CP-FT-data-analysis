from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, create_engine, text


@dataclass(frozen=True, slots=True)
class PublishedRun:
    processing_run_id: int
    source_file_id: int
    finished_at_utc: datetime | None
    started_at_utc: datetime | None
    is_current: bool
    current_version_ids: tuple[int, ...]

    @property
    def order_key(self) -> tuple[datetime, int]:
        observed = self.finished_at_utc or self.started_at_utc
        if observed is None:
            observed = datetime.min.replace(tzinfo=UTC)
        elif observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        else:
            observed = observed.astimezone(UTC)
        return observed, self.processing_run_id


@dataclass(frozen=True, slots=True)
class SourceRepair:
    source_file_id: int
    winner_run_id: int
    loser_run_ids: tuple[int, ...]
    superseded_current_version_ids: tuple[int, ...]
    predecessor_run_id: int | None


def choose_source_repair(runs: tuple[PublishedRun, ...]) -> SourceRepair | None:
    if not runs:
        return None
    sources = {item.source_file_id for item in runs}
    if len(sources) != 1:
        raise ValueError("published runs must belong to exactly one source file")
    candidates = [item for item in runs if item.current_version_ids]
    if not candidates:
        return None
    winner = max(candidates, key=lambda item: item.order_key)
    newer_unbound = [
        item
        for item in runs
        if not item.current_version_ids and item.order_key > winner.order_key
    ]
    if newer_unbound:
        raise ValueError(
            "a newer published run has no Current Dataset Version; "
            "automatic winner selection is unsafe"
        )
    losers = tuple(
        sorted(
            (item for item in runs if item.processing_run_id != winner.processing_run_id),
            key=lambda item: item.order_key,
        )
    )
    version_ids = tuple(
        sorted(
            {
                version_id
                for item in losers
                for version_id in item.current_version_ids
            }
        )
    )
    predecessors = [item for item in losers if item.order_key < winner.order_key]
    predecessor = predecessors[-1].processing_run_id if predecessors else None
    if winner.is_current and not losers and not version_ids:
        return None
    return SourceRepair(
        source_file_id=winner.source_file_id,
        winner_run_id=winner.processing_run_id,
        loser_run_ids=tuple(item.processing_run_id for item in losers),
        superseded_current_version_ids=version_ids,
        predecessor_run_id=predecessor,
    )


def _build_plan(connection: Connection, *, lock: bool) -> tuple[SourceRepair, ...]:
    lock_hint = " WITH (UPDLOCK,HOLDLOCK)" if lock else ""
    rows = (
        connection.execute(
            text(
                "SELECT pr.processing_run_id,pr.source_file_id,pr.finished_at_utc,"
                "pr.started_at_utc,pr.is_current,dv.dataset_version_id,"
                "CASE WHEN dv.status='PUBLISHED' AND dv.is_current=1 THEN 1 ELSE 0 END "
                "AS version_is_current,"
                "(SELECT COUNT(*) FROM dataset.dataset_version_run all_links "
                "WHERE all_links.dataset_version_id=dv.dataset_version_id) AS run_link_count "
                f"FROM ingestion.processing_run pr{lock_hint} "
                "LEFT JOIN dataset.dataset_version_run dvr "
                "ON dvr.processing_run_id=pr.processing_run_id "
                "LEFT JOIN dataset.dataset_version dv "
                "ON dv.dataset_version_id=dvr.dataset_version_id "
                "WHERE pr.status='PUBLISHED' AND pr.source_file_id IS NOT NULL "
                "ORDER BY pr.source_file_id,pr.processing_run_id,dv.dataset_version_id"
            )
        )
        .mappings()
        .all()
    )
    grouped: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        source_id = int(row["source_file_id"])
        run_id = int(row["processing_run_id"])
        item = grouped[source_id].setdefault(
            run_id,
            {
                "processing_run_id": run_id,
                "source_file_id": source_id,
                "finished_at_utc": row["finished_at_utc"],
                "started_at_utc": row["started_at_utc"],
                "is_current": bool(row["is_current"]),
                "current_version_ids": [],
            },
        )
        if bool(row["version_is_current"]):
            if int(row["run_link_count"] or 0) != 1:
                raise RuntimeError(
                    "current Dataset Version with multiple Processing Runs requires "
                    "a business-specific repair"
                )
            item["current_version_ids"].append(int(row["dataset_version_id"]))

    repairs: list[SourceRepair] = []
    for source_id in sorted(grouped):
        runs = tuple(
            PublishedRun(
                processing_run_id=int(item["processing_run_id"]),
                source_file_id=int(item["source_file_id"]),
                finished_at_utc=item["finished_at_utc"],
                started_at_utc=item["started_at_utc"],
                is_current=bool(item["is_current"]),
                current_version_ids=tuple(sorted(item["current_version_ids"])),
            )
            for item in grouped[source_id].values()
        )
        repair = choose_source_repair(runs)
        if repair is not None:
            repairs.append(repair)
    return tuple(repairs)


def _plan_payload(plan: tuple[SourceRepair, ...]) -> dict[str, object]:
    return {
        "policy": "LATEST_PUBLISHED_CURRENT_DATASET_RUN_V1",
        "history_preserved": True,
        "source_count": len(plan),
        "winner_count": len(plan),
        "loser_run_count": sum(len(item.loser_run_ids) for item in plan),
        "superseded_current_version_count": sum(
            len(item.superseded_current_version_ids) for item in plan
        ),
        "repairs": [asdict(item) for item in plan],
    }


def _plan_sha256(plan: tuple[SourceRepair, ...]) -> str:
    payload = json.dumps(
        _plan_payload(plan), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run or repair legacy Processing Run Current state while preserving "
            "all historical rows"
        )
    )
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-revision", default="sql2014_0018")
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
                for version_id in item.superseded_current_version_ids:
                    updated = connection.execute(
                        text(
                            "UPDATE dataset.dataset_version SET status='SUPERSEDED',"
                            "is_current=0 WHERE dataset_version_id=:version "
                            "AND status='PUBLISHED' AND is_current=1"
                        ),
                        {"version": version_id},
                    )
                    if updated.rowcount != 1:
                        raise RuntimeError(
                            f"Dataset Version {version_id} changed during repair"
                        )
                for run_id in item.loser_run_ids:
                    updated = connection.execute(
                        text(
                            "UPDATE ingestion.processing_run SET status='SUPERSEDED',"
                            "is_current=0 WHERE processing_run_id=:run "
                            "AND source_file_id=:source AND status='PUBLISHED'"
                        ),
                        {"run": run_id, "source": item.source_file_id},
                    )
                    if updated.rowcount != 1:
                        raise RuntimeError(
                            f"Processing Run {run_id} changed during repair"
                        )
                parameters = {
                    "run": item.winner_run_id,
                    "source": item.source_file_id,
                    "previous": item.predecessor_run_id,
                }
                winner = connection.execute(
                    text(
                        "UPDATE ingestion.processing_run SET is_current=1,"
                        "supersedes_processing_run_id=COALESCE(:previous,"
                        "supersedes_processing_run_id) "
                        "WHERE processing_run_id=:run AND source_file_id=:source "
                        "AND status='PUBLISHED'"
                    ),
                    parameters,
                )
                if winner.rowcount != 1:
                    raise RuntimeError(
                        f"winner Processing Run {item.winner_run_id} changed during repair"
                    )
                connection.execute(
                    text(
                        "INSERT governance.audit_log(actor,operation,entity_type,"
                        "entity_id,before_json,after_json,reason,correlation_id) "
                        "VALUES('script:repair_processing_run_current',"
                        "'PROCESSING_RUN_CURRENT_REPAIR','ingestion.source_file',"
                        ":entity,:before_json,:after_json,:reason,:correlation)"
                    ),
                    {
                        "entity": str(item.source_file_id),
                        "before_json": json.dumps(
                            {
                                "published_run_ids": [
                                    *item.loser_run_ids,
                                    item.winner_run_id,
                                ],
                                "current_version_ids_to_supersede": (
                                    item.superseded_current_version_ids
                                ),
                            },
                            separators=(",", ":"),
                        ),
                        "after_json": json.dumps(
                            {
                                "winner_run_id": item.winner_run_id,
                                "history_preserved": True,
                            },
                            separators=(",", ":"),
                        ),
                        "reason": "Normalize legacy Current state before TMS v1 gray release",
                        "correlation": f"processing-run-current-repair:{digest}",
                    },
                )

            remaining = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM dataset.dataset_version dv "
                        "WHERE dv.status='PUBLISHED' AND dv.is_current=1 AND EXISTS("
                        "SELECT 1 FROM dataset.dataset_version_run dvr "
                        "JOIN ingestion.processing_run pr "
                        "ON pr.processing_run_id=dvr.processing_run_id "
                        "WHERE dvr.dataset_version_id=dv.dataset_version_id "
                        "AND (pr.status<>'PUBLISHED' OR pr.is_current<>1))"
                    )
                ).scalar_one()
            )
            if remaining:
                raise RuntimeError(
                    f"repair left {remaining} Dataset Current anomalies"
                )
        print("processing_run_current_repair=PASS history_preserved=true")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
