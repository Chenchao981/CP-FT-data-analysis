"""Verify physical stage storage and public-ID continuity on the development DB."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.stage_fact_repository import insert_measurements, insert_units


def snapshot(c) -> dict:
    result = {}
    for table in (
        "test.unit_result",
        "test.measurement",
        "test.unit_bin_evaluation",
        "test.measurement_evaluation",
        "trace.unit_traceability",
    ):
        result[table] = int(
            c.execute(text(f"SELECT COUNT_BIG(*) FROM {table}")).scalar_one()
        )
    result["versions"] = [
        list(r)
        for r in c.execute(
            text(
                "SELECT dataset_version_id,dataset_id,version_no,status,is_current,spec_set_id "
                "FROM dataset.dataset_version ORDER BY dataset_version_id"
            )
        )
    ]
    result["by_run"] = [
        list(r)
        for r in c.execute(
            text(
                "WITH units AS (SELECT run_id,COUNT_BIG(*) n,SUM(CASE WHEN overall_result='PASS' THEN CONVERT(bigint,1) ELSE 0 END) p "
                "FROM test.unit_result GROUP BY run_id), measurements AS ("
                "SELECT u.run_id,COUNT_BIG(*) n FROM test.measurement m JOIN test.unit_result u ON u.unit_id=m.unit_id GROUP BY u.run_id) "
                "SELECT r.run_id,r.test_stage,r.lot_id,COALESCE(u.n,0),COALESCE(u.p,0),COALESCE(m.n,0) FROM test.test_run r "
                "LEFT JOIN units u ON u.run_id=r.run_id LEFT JOIN measurements m ON m.run_id=r.run_id ORDER BY r.run_id"
            )
        )
    ]
    return result


def probes(engine) -> dict:
    with engine.connect() as c:
        runs = dict(
            c.execute(
                text(
                    "SELECT test_stage,MIN(run_id) FROM test.test_run GROUP BY test_stage"
                )
            ).all()
        )
        item = int(
            c.execute(
                text("SELECT MIN(test_item_id) FROM mdm.test_item_definition")
            ).scalar_one()
        )

    def write_pair(stage):
        with engine.connect() as c:
            tx = c.begin()
            try:
                unit = insert_units(
                    c,
                    stage,
                    [
                        {
                            "run_id": runs[stage],
                            "logical_unit_key": "PHYSICAL-SQL-PROBE",
                            "unit_sequence": 1,
                        }
                    ],
                )[0]
                measurement = insert_measurements(
                    c,
                    stage,
                    [
                        {
                            "unit_id": unit,
                            "test_item_id": item,
                            "value_numeric": 1.2345678901234567,
                            "raw_value": "1.2345678901234567 ",
                            "measurement_status": "MEASURED",
                        }
                    ],
                )[0]
                assert (
                    c.execute(
                        text(
                            "SELECT value_numeric FROM test.measurement WHERE measurement_id=:id"
                        ),
                        {"id": measurement},
                    ).scalar_one()
                    == 1.2345678901234567
                )
                c.execute(
                    text("DELETE FROM test.measurement WHERE measurement_id=:id"),
                    {"id": measurement},
                )
                c.execute(
                    text("DELETE FROM test.unit_result WHERE unit_id=:id"), {"id": unit}
                )
                assert (
                    c.execute(
                        text(
                            "SELECT COUNT(*) FROM test.unit_identity WHERE unit_id=:id"
                        ),
                        {"id": unit},
                    ).scalar_one()
                    == 0
                )
                assert (
                    c.execute(
                        text(
                            "SELECT COUNT(*) FROM test.measurement_identity WHERE measurement_id=:id"
                        ),
                        {"id": measurement},
                    ).scalar_one()
                    == 0
                )
                return unit, measurement
            finally:
                tx.rollback()

    with ThreadPoolExecutor(max_workers=2) as pool:
        pairs = list(pool.map(write_pair, ("CP", "FT")))
    assert len({p[0] for p in pairs}) == 2 and len({p[1] for p in pairs}) == 2
    rejected = []
    for label, expected_error, sql in (
        (
            "archive_write",
            51028,
            "UPDATE test.unit_result_legacy_0027 SET unit_sequence=unit_sequence WHERE unit_id=(SELECT MIN(unit_id) FROM test.unit_result_legacy_0027)",
        ),
        (
            "cross_stage_unit",
            547,
            "INSERT test.cp_die(unit_id,run_id,logical_unit_key) SELECT TOP(1) unit_id,run_id,logical_unit_key FROM test.ft_device",
        ),
        (
            "cross_stage_measurement",
            547,
            "INSERT test.cp_measurement(measurement_id,unit_id,test_item_id,measurement_status) SELECT TOP(1) measurement_id,unit_id,test_item_id,measurement_status FROM test.ft_measurement",
        ),
        (
            "dangling_trace",
            547,
            "INSERT trace.unit_traceability(source_unit_id,target_unit_id,trace_type) VALUES(-1,-2,'CP_TO_FT')",
        ),
        (
            "compatibility_insert",
            4436,
            "INSERT test.unit_result(run_id,logical_unit_key) VALUES(1,N'INVALID-LEGACY-WRITE')",
        ),
        (
            "compatibility_update",
            4436,
            "UPDATE test.measurement SET value_numeric=0 WHERE measurement_id=-1",
        ),
    ):
        with engine.connect() as c:
            tx = c.begin()
            try:
                try:
                    c.execute(text(sql))
                except DBAPIError as exc:
                    if f"({expected_error})" not in str(exc.orig):
                        raise AssertionError(
                            f"{label} failed for an unexpected SQL error"
                        ) from exc
                    rejected.append(label)
                else:
                    raise ValueError(f"invalid write accepted: {label}")
            finally:
                tx.rollback()
    return {
        "concurrent_CP_FT_ids_distinct": True,
        "public_view_reads_and_deletes": "PASS",
        "rejections": rejected,
        "probe_transactions": "ROLLED_BACK",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", action="store_true")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--probe-writes", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    identity = check_database()
    expected = "sql2014_0027" if args.before else "sql2014_0029"
    if identity["database"] != "TMS_G0_DEV" or identity["schema_revision"] != expected:
        raise ValueError("physical verification database/revision mismatch")
    engine = get_engine()
    with engine.connect() as c:
        facts = snapshot(c)
        layout = {}
        if not args.before:
            for table in (
                "cp_die",
                "ft_device",
                "cp_measurement",
                "ft_measurement",
                "unit_identity",
                "measurement_identity",
            ):
                assert (
                    c.execute(
                        text("SELECT OBJECT_ID(:name,'U')"), {"name": "test." + table}
                    ).scalar_one()
                    is not None
                )
                layout[table] = int(
                    c.execute(
                        text(f"SELECT COUNT_BIG(*) FROM test.{table}")
                    ).scalar_one()
                )
            assert (
                layout["cp_die"] + layout["ft_device"]
                == layout["unit_identity"]
                == facts["test.unit_result"]
            )
            assert (
                layout["cp_measurement"] + layout["ft_measurement"]
                == layout["measurement_identity"]
                == facts["test.measurement"]
            )
            for name in ("test.unit_result", "test.measurement"):
                assert (
                    c.execute(
                        text("SELECT OBJECT_ID(:name,'V')"), {"name": name}
                    ).scalar_one()
                    is not None
                )
            assert (
                c.execute(
                    text(
                        "SELECT COUNT(*) FROM sys.columns WHERE object_id=OBJECT_ID('test.ft_device') AND name IN('wafer_id','x_coord','y_coord')"
                    )
                ).scalar_one()
                == 0
            )
            assert (
                c.execute(
                    text(
                        "SELECT COUNT(*) FROM sys.foreign_keys WHERE is_disabled=1 OR is_not_trusted=1"
                    )
                ).scalar_one()
                == 0
            )
            assert (
                c.execute(
                    text(
                        "SELECT COUNT(*) FROM sys.sql_expression_dependencies d JOIN sys.views v ON d.referencing_id=v.object_id "
                        "WHERE d.referenced_id IN(OBJECT_ID('test.unit_result_legacy_0027'),OBJECT_ID('test.measurement_legacy_0027'))"
                    )
                ).scalar_one()
                == 0
            )
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        if baseline["facts"] != facts:
            raise ValueError("original facts or Dataset versions changed")
    result = {
        "verification": "PASS",
        "identity": identity,
        "facts": facts,
        "layout": layout,
    }
    if args.probe_writes:
        if args.before:
            raise ValueError("probes require physical schema")
        result["write_probes"] = probes(engine)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"verification": "PASS", "layout": layout}))


if __name__ == "__main__":
    main()
