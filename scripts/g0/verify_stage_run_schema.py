"""Read-only CP/FT field reconciliation and per-run fact fingerprints."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.infrastructure.database import check_database, get_engine
from app.infrastructure.stage_run_details import (
    persist_stage_run_details,
    stage_detail_values,
)


def probe_writes() -> dict:
    """Exercise the actual Writer helper and database rejection paths; always roll back."""
    if check_database()["database"] != "TMS_G0_DEV":
        raise ValueError("write probes require the test database")
    engine = get_engine()
    with engine.connect() as c:
        cp_run = int(
            c.execute(text("SELECT MIN(run_id) FROM test.cp_run_detail")).scalar_one()
        )
        ft_run = int(
            c.execute(text("SELECT MIN(run_id) FROM test.ft_run_detail")).scalar_one()
        )
        ft_spec = int(
            c.execute(
                text("SELECT MIN(source_spec_set_id) FROM test.ft_run_detail")
            ).scalar_one()
        )
    rejected = []
    for label, sql, params in (
        (
            "cross_stage_run",
            "INSERT test.cp_run_detail(run_id) VALUES(:run)",
            {"run": ft_run},
        ),
        (
            "cross_stage_spec",
            "UPDATE test.cp_run_detail SET source_spec_set_id=:spec WHERE run_id=:run",
            {"run": cp_run, "spec": ft_spec},
        ),
        (
            "empty_ft_source",
            "UPDATE test.ft_run_detail SET source_id=N' ' WHERE run_id=:run",
            {"run": ft_run},
        ),
    ):
        with engine.connect() as c:
            transaction = c.begin()
            try:
                try:
                    c.execute(text(sql), params)
                except IntegrityError:
                    rejected.append(label)
                else:
                    raise ValueError(f"database accepted invalid write: {label}")
            finally:
                transaction.rollback()
    replayed = []
    for stage, run_id, table in (
        ("CP", cp_run, "test.cp_run_detail"),
        ("FT", ft_run, "test.ft_run_detail"),
    ):
        with engine.connect() as c:
            transaction = c.begin()
            try:
                processing = int(
                    c.execute(
                        text(
                            "SELECT processing_run_id FROM test.test_run WHERE run_id=:run"
                        ),
                        {"run": run_id},
                    ).scalar_one()
                )
                where = " WHERE run_id IN(SELECT run_id FROM test.test_run WHERE processing_run_id=:processing)"
                params = {"processing": processing}
                previous = c.execute(
                    text(f"SELECT * FROM {table}" + where + " ORDER BY run_id"), params
                ).all()
                c.execute(text(f"DELETE {table}" + where), params)
                persist_stage_run_details(c, processing_run_id=processing)
                actual = c.execute(
                    text(f"SELECT * FROM {table}" + where + " ORDER BY run_id"), params
                ).all()
                if previous != actual:
                    raise ValueError("Writer replay differs from migration backfill")
                replayed.append(stage)
            finally:
                transaction.rollback()
    return {
        "rejected": rejected,
        "writer_replay": replayed,
        "all_transactions": "ROLLED_BACK",
    }


def verify(*, before: bool = False) -> dict:
    identity = check_database()
    expected = "sql2014_0026" if before else "sql2014_0029"
    if identity["database"] != "TMS_G0_DEV" or identity["schema_revision"] != expected:
        raise ValueError("stage schema verification database/revision mismatch")
    with get_engine().connect() as c:
        runs = (
            c.execute(
                text(
                    "SELECT run_id,test_stage,metadata_json FROM test.test_run "
                    "WHERE test_stage IN('CP','FT') ORDER BY run_id"
                )
            )
            .mappings()
            .all()
        )
        fields = {"CP": [], "FT": []}
        for r in runs:
            values = stage_detail_values(r["test_stage"], r["metadata_json"])
            fields[r["test_stage"]].append({"run_id": r["run_id"], **values})
        counts = {}
        for name in (
            "test.unit_result",
            "test.measurement",
            "dataset.dataset_version",
            "test.measurement_evaluation",
            "test.unit_bin_evaluation",
        ):
            counts[name] = int(
                c.execute(text(f"SELECT COUNT_BIG(*) FROM {name}")).scalar_one()
            )
        unit_facts = [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT run_id,overall_result,COUNT_BIG(*) row_count,MIN(unit_id) first_id,"
                    "MAX(unit_id) last_id,SUM(CONVERT(bigint,BINARY_CHECKSUM(unit_id,logical_unit_key,"
                    "attempt_no,unit_sequence,wafer_id,x_coord,y_coord,soft_bin,hard_bin,overall_result))) fingerprint "
                    "FROM test.unit_result GROUP BY run_id,overall_result ORDER BY run_id,overall_result"
                )
            )
        ]
        measurement_facts = [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT u.run_id,m.measurement_status,COUNT_BIG(*) row_count,MIN(m.measurement_id) first_id,"
                    "MAX(m.measurement_id) last_id,SUM(CONVERT(bigint,BINARY_CHECKSUM(m.measurement_id,m.unit_id,"
                    "m.test_item_id,m.value_numeric,m.value_text,m.raw_value,m.measurement_status,"
                    "m.tester_pass_flag,m.source_column_index))) fingerprint "
                    "FROM test.measurement m JOIN test.unit_result u ON u.unit_id=m.unit_id "
                    "GROUP BY u.run_id,m.measurement_status ORDER BY u.run_id,m.measurement_status"
                )
            )
        ]
        if not before:
            for stage, table in (
                ("CP", "test.cp_run_detail"),
                ("FT", "test.ft_run_detail"),
            ):
                rows = [
                    dict(r._mapping)
                    for r in c.execute(text(f"SELECT * FROM {table} ORDER BY run_id"))
                ]
                for r in rows:
                    if r.pop("test_stage") != stage:
                        raise ValueError("stage detail discriminator mismatch")
                if rows != fields[stage]:
                    raise ValueError(
                        f"{stage} relational fields differ from source evidence"
                    )
                base_count = int(
                    c.execute(
                        text(
                            "SELECT COUNT_BIG(*) FROM test.unit_result u JOIN test.test_run r ON r.run_id=u.run_id "
                            "WHERE r.test_stage=:stage"
                        ),
                        {"stage": stage},
                    ).scalar_one()
                )
                view = "test.v_cp_die" if stage == "CP" else "test.v_ft_device"
                if (
                    int(
                        c.execute(text(f"SELECT COUNT_BIG(*) FROM {view}")).scalar_one()
                    )
                    != base_count
                ):
                    raise ValueError(f"{stage} view coverage mismatch")
            bad = int(
                c.execute(
                    text(
                        "SELECT COUNT(*) FROM sys.foreign_keys WHERE is_disabled=1 OR is_not_trusted=1"
                    )
                ).scalar_one()
            )
            if bad:
                raise ValueError("untrusted or disabled foreign keys")
    return {
        "identity": identity,
        "counts": counts,
        "fields": fields,
        "unit_facts": unit_facts,
        "measurement_facts": measurement_facts,
        "fingerprint_note": "BINARY_CHECKSUM aggregates are drift checks, not cryptographic equality proofs",
        "verification": "PASS",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", action="store_true")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--probe-writes", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(before=args.before)
    if args.probe_writes:
        if args.before:
            raise ValueError("write probes require the migrated schema")
        result["write_probes"] = probe_writes()
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        for key in ("counts", "fields", "unit_facts", "measurement_facts"):
            if result[key] != baseline[key]:
                raise ValueError(f"migration changed baseline: {key}")
        result["baseline_reconciliation"] = "PASS"
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "verification": "PASS",
                "counts": result["counts"],
                "cp_runs": len(result["fields"]["CP"]),
                "ft_runs": len(result["fields"]["FT"]),
            }
        )
    )


if __name__ == "__main__":
    main()
