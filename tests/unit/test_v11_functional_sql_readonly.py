from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from scripts.g0.verify_v11_functional_sql_readonly import (
    CatalogSnapshot,
    ReadOnlyAudit,
    VerificationError,
    _assert_catalog_snapshot_unchanged,
    _assert_read_only_sql,
    _assert_snapshot_unchanged,
    _counts_from_row,
    _identity,
    _ReadOnlyConnection,
    _select_targets,
    verify,
)


class FakeResult:
    def __init__(self, *, rows=None, scalar=None) -> None:
        self.rows = list(rows or [])
        self.scalar = scalar

    def mappings(self):
        return self

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.scalar


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        return FakeResult(scalar=1)


class IdentityConnection:
    def __init__(
        self,
        *,
        database: str = "TMS_G0_DEV",
        revision: str = "sql2014_0019",
        banner: str = "Microsoft SQL Server 2014",
    ) -> None:
        self.database = database
        self.revision = revision
        self.banner = banner

    def execute(self, statement, parameters=None):
        del statement, parameters
        return FakeResult(
            rows=[
                {
                    "database_name": self.database,
                    "schema_revision": self.revision,
                    "product_version": "12.0.6449.1",
                    "engine_edition": 3,
                    "version_banner": self.banner,
                }
            ]
        )


def test_read_only_guard_accepts_select_and_read_only_cte() -> None:
    _assert_read_only_sql("SELECT 'DELETE is data' AS note;")
    _assert_read_only_sql(
        "WITH source_rows AS (SELECT 1 AS value) SELECT value FROM source_rows"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE test.unit_result SET overall_result='PASS'",
        "WITH target AS (SELECT 1 AS id) DELETE FROM target",
        "SELECT * INTO #copy FROM test.unit_result",
        "SELECT 1; DROP TABLE test.measurement",
        "EXEC dbo.some_procedure",
        "",
    ],
)
def test_read_only_guard_rejects_mutation_and_multi_statement_sql(sql: str) -> None:
    with pytest.raises(VerificationError):
        _assert_read_only_sql(sql)


def test_read_only_connection_blocks_before_database_execution() -> None:
    raw = RecordingConnection()
    audit = ReadOnlyAudit()
    guarded = _ReadOnlyConnection(raw, audit)

    guarded.execute(text("SELECT 1"))
    with pytest.raises(VerificationError) as error:
        guarded.execute(text("ALTER TABLE test.unit_result ADD invalid int NULL"))

    assert error.value.code == "READ_ONLY_STATEMENT_REJECTED"
    assert len(raw.calls) == 1
    assert audit.statement_count == 1
    assert audit.blocked_statement_count == 1


def test_identity_requires_exact_database_revision_and_sql_server() -> None:
    assert _identity(IdentityConnection()) == {
        "database": "TMS_G0_DEV",
        "schema_revision": "sql2014_0019",
        "database_engine": "Microsoft SQL Server",
        "product_major": 12,
        "engine_edition": 3,
    }

    with pytest.raises(VerificationError) as database_error:
        _identity(IdentityConnection(database="master"))
    assert database_error.value.code == "DATABASE_IDENTITY_MISMATCH"

    with pytest.raises(VerificationError) as revision_error:
        _identity(IdentityConnection(revision="sql2014_0017"))
    assert revision_error.value.code == "SCHEMA_REVISION_MISMATCH"

    with pytest.raises(VerificationError) as engine_error:
        _identity(IdentityConnection(banner="PostgreSQL"))
    assert engine_error.value.code == "DATABASE_ENGINE_MISMATCH"


def test_snapshot_requires_canonical_and_current_counts_to_be_unchanged() -> None:
    before = {
        "canonical": {"test_run": 3, "unit_result": 7, "measurement": 20},
        "current": {
            "dataset_version": 2,
            "test_run": 3,
            "unit_result": 7,
            "measurement": 20,
        },
    }
    _assert_snapshot_unchanged(before, before.copy())

    after = {
        **before,
        "current": {**before["current"], "measurement": 21},
    }
    with pytest.raises(VerificationError) as error:
        _assert_snapshot_unchanged(before, after)
    assert error.value.code == "READ_ONLY_SNAPSHOT_DRIFT"


def test_catalog_snapshot_detects_same_count_membership_drift() -> None:
    before = CatalogSnapshot(
        rows=(
            (1, 10, 1, "CP", "LOT-A"),
            (2, 20, 1, "FT", "LOT-B"),
        )
    )
    unchanged = CatalogSnapshot(rows=before.rows)
    _assert_catalog_snapshot_unchanged(before, unchanged)

    same_counts_different_member = CatalogSnapshot(
        rows=(
            (1, 10, 1, "CP", "LOT-A"),
            (2, 20, 1, "FT", "LOT-C"),
        )
    )
    assert before.public()["canonical_lot_member_count"] == 2
    assert same_counts_different_member.public()["canonical_lot_member_count"] == 2
    with pytest.raises(VerificationError) as error:
        _assert_catalog_snapshot_unchanged(before, same_counts_different_member)
    assert error.value.code == "READ_ONLY_CATALOG_SNAPSHOT_DRIFT"


def test_result_counts_keep_unknown_and_abort_out_of_yield() -> None:
    counts = _counts_from_row(
        {
            "total_units": 12,
            "pass_units": 9,
            "fail_units": 1,
            "unknown_units": 1,
            "abort_units": 1,
            "other_units": 0,
        }
    )

    assert counts.known_yield_denominator == 10
    assert counts.yield_rate == pytest.approx(0.9)

    with pytest.raises(VerificationError) as error:
        _counts_from_row(
            {
                "total_units": 12,
                "pass_units": 9,
                "fail_units": 1,
                "unknown_units": 1,
                "abort_units": 0,
                "other_units": 0,
            }
        )
    assert error.value.code == "RESULT_COUNT_NOT_RECONCILED"


class CandidateConnection:
    def execute(self, statement, parameters=None):
        del statement
        stage = parameters["stage"]
        published = datetime(2026, 8, 30, 8, 0)
        if stage == "FT":
            rows = [
                {
                    "dataset_id": 8,
                    "dataset_version_id": 80,
                    "version_no": 1,
                    "test_stage": "FT",
                    "spec_set_id": None,
                    "published_at_utc": published,
                },
                {
                    "dataset_id": 9,
                    "dataset_version_id": 90,
                    "version_no": 1,
                    "test_stage": "FT",
                    "spec_set_id": None,
                    "published_at_utc": published,
                },
            ]
        else:
            rows = [
                {
                    "dataset_id": 1,
                    "dataset_version_id": 10,
                    "version_no": 1,
                    "test_stage": "CP",
                    "spec_set_id": None,
                    "published_at_utc": published,
                },
                {
                    "dataset_id": 2,
                    "dataset_version_id": 20,
                    "version_no": 1,
                    "test_stage": "CP",
                    "spec_set_id": 100,
                    "published_at_utc": published,
                },
                {
                    "dataset_id": 3,
                    "dataset_version_id": 30,
                    "version_no": 1,
                    "test_stage": "CP",
                    "spec_set_id": 100,
                    "published_at_utc": published,
                },
            ]
        return FakeResult(rows=rows)


def test_target_selection_uses_two_ft_and_only_proven_compatible_cp() -> None:
    connection = CandidateConnection()

    cp_targets = _select_targets(connection, "CP")
    ft_targets = _select_targets(connection, "FT")

    assert [target.dataset_id for target in cp_targets] == [2, 3]
    assert {target.spec_set_id for target in cp_targets} == {100}
    assert [target.dataset_id for target in ft_targets] == [8, 9]


_COUNTS = {
    1001: {
        "total_units": 4,
        "pass_units": 2,
        "fail_units": 1,
        "unknown_units": 1,
        "abort_units": 0,
        "other_units": 0,
    },
    2002: {
        "total_units": 3,
        "pass_units": 0,
        "fail_units": 0,
        "unknown_units": 3,
        "abort_units": 0,
        "other_units": 0,
    },
}
_UNIT_IDS = {1001: (11, 12, 13, 14), 2002: (21, 22, 23)}
_CATALOG_SNAPSHOT_ROWS = (
    {
        "dataset_id": 101,
        "dataset_version_id": 1001,
        "version_no": 1,
        "test_stage": "CP",
        "lot_id": "SECRET-CP-LOT",
    },
    {
        "dataset_id": 202,
        "dataset_version_id": 2002,
        "version_no": 1,
        "test_stage": "FT",
        "lot_id": "SECRET-FT-LOT-A",
    },
    {
        "dataset_id": 202,
        "dataset_version_id": 2002,
        "version_no": 1,
        "test_stage": "FT",
        "lot_id": "SECRET-FT-LOT-B",
    },
    {
        "dataset_id": 303,
        "dataset_version_id": 3003,
        "version_no": 1,
        "test_stage": "FT",
        "lot_id": None,
    },
)


class VerificationConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        parameters = parameters or {}
        self.statements.append(sql)
        if "@@VERSION" in sql:
            return FakeResult(
                rows=[
                    {
                        "database_name": "TMS_G0_DEV",
                        "schema_revision": "sql2014_0019",
                        "product_version": "12.0.6449.1",
                        "engine_edition": 3,
                        "version_banner": "Microsoft SQL Server 2014",
                    }
                ]
            )
        snapshots = {
            "FROM test.test_run": 2,
            "FROM test.unit_result": 7,
            "FROM test.measurement": 14,
            "FROM analytics.v_current_dataset_version": 2,
            "FROM analytics.v_current_test_run": 2,
            "FROM analytics.v_current_unit_result": 7,
            "FROM analytics.v_current_measurement": 14,
        }
        if sql.startswith("SELECT COUNT_BIG(*) FROM"):
            for fragment, count in snapshots.items():
                if fragment in sql:
                    return FakeResult(scalar=count)
        if sql.startswith("SELECT DISTINCT d.dataset_id"):
            return FakeResult(rows=_CATALOG_SNAPSHOT_ROWS)
        if "SELECT TOP (32)" in sql:
            stage = parameters["stage"]
            if stage == "CP":
                row = {
                    "dataset_id": 101,
                    "dataset_version_id": 1001,
                    "version_no": 1,
                    "test_stage": "CP",
                    "spec_set_id": 77,
                    "published_at_utc": datetime(2026, 8, 30, 8, 0),
                }
            else:
                row = {
                    "dataset_id": 202,
                    "dataset_version_id": 2002,
                    "version_no": 1,
                    "test_stage": "FT",
                    "spec_set_id": None,
                    "published_at_utc": datetime(2026, 8, 30, 9, 0),
                }
            return FakeResult(rows=[row])
        if (
            "FROM analytics.v_current_unit_result" in sql
            and "AS total_units" in sql
            and "JOIN analytics" not in sql
        ):
            return FakeResult(rows=[_COUNTS[int(parameters["dataset_version_id"])]])
        if sql.startswith("SELECT unit_id FROM analytics.v_current_unit_result"):
            ids = _UNIT_IDS[int(parameters["dataset_version_id"])]
            offset = int(parameters["offset"])
            limit = int(parameters["page_size"])
            return FakeResult(
                rows=[(unit_id,) for unit_id in ids[offset : offset + limit]]
            )
        if "COUNT(DISTINCT cdv.dataset_version_id)" in sql:
            version_id = 1001 if parameters["stage"] == "CP" else 2002
            return FakeResult(rows=[{"dataset_count": 1, **_COUNTS[version_id]}])
        raise AssertionError(sql)


class VerificationEngine:
    def __init__(self) -> None:
        self.connection = VerificationConnection()

    @contextmanager
    def connect(self):
        yield self.connection


def _target_for_dataset(dataset_id: int) -> tuple[str, int]:
    return ("CP", 1001) if dataset_id == 101 else ("FT", 2002)


class FakeDatasetService:
    def __init__(self, engine) -> None:
        self.engine = engine

    def compare(self, request):
        dataset_id = request.datasets[0].dataset_id
        stage, version_id = _target_for_dataset(dataset_id)
        counts = _COUNTS[version_id]
        known = counts["pass_units"] + counts["fail_units"]
        item = SimpleNamespace(
            dataset_id=dataset_id,
            version_no=1,
            unit_count=counts["total_units"],
            pass_count=counts["pass_units"],
            fail_count=counts["fail_units"],
            unknown_count=counts["unknown_units"],
            abort_count=counts["abort_units"],
            known_yield_denominator=known,
            yield_rate=counts["pass_units"] / known if known else None,
        )
        return SimpleNamespace(
            test_stage=stage,
            spec_compatibility="SINGLE_DATASET",
            items=(item,),
        )

    def get_detail_page(
        self,
        dataset_id,
        version_no,
        *,
        page,
        page_size,
        parameters=(),
    ):
        stage, version_id = _target_for_dataset(dataset_id)
        ids = _UNIT_IDS[version_id]
        offset = (page - 1) * page_size
        visible = ids[offset : offset + page_size]
        status_by_id = {
            11: "PASS",
            12: "PASS",
            13: "FAIL",
            14: "UNKNOWN",
            21: "UNKNOWN",
            22: "UNKNOWN",
            23: "UNKNOWN",
        }
        items = tuple(
            SimpleNamespace(
                unit_id=unit_id,
                overall_result=status_by_id[unit_id],
                measurements=(
                    (SimpleNamespace(parameter=parameters[0]),) if parameters else ()
                ),
            )
            for unit_id in visible
        )
        return SimpleNamespace(
            dataset_id=dataset_id,
            version_no=version_no,
            test_stage=stage,
            page=page,
            page_size=page_size,
            total=len(ids),
            parameter_options=("SECRET_PARAMETER",),
            items=items,
        )


class FakeCurrentCatalogService:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.records = (
            SimpleNamespace(
                dataset_id=101,
                dataset_version_id=1001,
                version_no=1,
                test_stage="CP",
                lot_id="SECRET-CP-LOT",
                lot_count=1,
            ),
            SimpleNamespace(
                dataset_id=202,
                dataset_version_id=2002,
                version_no=1,
                test_stage="FT",
                lot_id=None,
                lot_count=2,
            ),
            SimpleNamespace(
                dataset_id=303,
                dataset_version_id=3003,
                version_no=1,
                test_stage="FT",
                lot_id=None,
                lot_count=0,
            ),
        )
        self.members = {
            1001: {"SECRET-CP-LOT"},
            2002: {"SECRET-FT-LOT-A", "SECRET-FT-LOT-B"},
            3003: set(),
        }

    def list_current_datasets(self, principal, filters):
        assert "SYSTEM_ADMIN" in principal.roles
        visible = self.records
        if filters.lot_id is not None:
            token = filters.lot_id.casefold()
            visible = tuple(
                item
                for item in visible
                if any(
                    token in member.casefold()
                    for member in self.members[item.dataset_version_id]
                )
            )
        offset = (filters.page - 1) * filters.page_size
        page_items = visible[offset : offset + filters.page_size]
        return SimpleNamespace(
            items=page_items,
            total=len(visible),
            page=filters.page,
            page_size=filters.page_size,
        )


class FakeManagementService:
    def __init__(self, engine) -> None:
        self.engine = engine

    def quality_summary(self, *, test_stage, **kwargs):
        del kwargs
        version_id = 1001 if test_stage == "CP" else 2002
        counts = _COUNTS[version_id]
        known = counts["pass_units"] + counts["fail_units"]
        total = counts["total_units"]
        return SimpleNamespace(
            kpis=SimpleNamespace(
                dataset_count=1,
                total_units=total,
                pass_units=counts["pass_units"],
                fail_units=counts["fail_units"],
                unknown_units=counts["unknown_units"],
                abort_units=counts["abort_units"],
                known_yield_denominator=known,
                yield_rate=counts["pass_units"] / known if known else None,
                unknown_rate=counts["unknown_units"] / total,
            )
        )


def test_verify_orchestrates_read_only_queries_and_redacts_business_identity() -> None:
    engine = VerificationEngine()

    evidence = verify(
        engine,  # type: ignore[arg-type]
        dataset_service_factory=FakeDatasetService,
        management_service_factory=FakeManagementService,
        current_catalog_service_factory=FakeCurrentCatalogService,
    )

    assert evidence["verification"] == "PASS"
    assert evidence["read_only"]["count_snapshot_unchanged"] is True
    assert evidence["read_only"]["full_catalog_snapshot_unchanged"] is True
    assert evidence["count_snapshot_before"] == evidence["count_snapshot_after"]
    assert evidence["current_catalog"] == {
        "verification": "PASS",
        "current_key_count": 3,
        "canonical_lot_member_count": 3,
        "distinct_lot_count": 3,
        "snapshot_sha256": evidence["current_catalog"]["snapshot_sha256"],
        "service_page_count": 2,
        "lot_filter_count": 3,
        "lot_filter_page_count": 3,
        "service_reconciliation_sha256": evidence["current_catalog"][
            "service_reconciliation_sha256"
        ],
        "lot_filter_reconciliation_sha256": evidence["current_catalog"][
            "lot_filter_reconciliation_sha256"
        ],
        "full_snapshot_unchanged": True,
    }
    for name in (
        "snapshot_sha256",
        "service_reconciliation_sha256",
        "lot_filter_reconciliation_sha256",
    ):
        assert len(evidence["current_catalog"][name]) == 64
    assert evidence["stages"]["CP"]["compare"]["combined_counts"] == {
        "total_units": 4,
        "pass_units": 2,
        "fail_units": 1,
        "unknown_units": 1,
        "abort_units": 0,
        "known_yield_denominator": 3,
        "yield_rate": pytest.approx(2 / 3),
    }
    assert evidence["stages"]["FT"]["details"][0]["beyond_last_page_empty"]
    assert all(
        statement.lstrip().upper().startswith(("SELECT", "WITH"))
        for statement in engine.connection.statements
    )

    business_evidence = {
        key: value for key, value in evidence.items() if key != "evidence_redaction"
    }
    serialized = json.dumps(business_evidence, ensure_ascii=False)
    for secret in (
        "SERVER-SECRET",
        "LOGIN-SECRET",
        "SECRET_PARAMETER",
        "SECRET-CP-LOT",
        "SECRET-FT-LOT-A",
        "SECRET-FT-LOT-B",
        '"dataset_id"',
        '"dataset_version_id"',
        '"unit_id"',
    ):
        assert secret not in serialized
    assert "connection_string" in evidence["evidence_redaction"]["omitted"]
