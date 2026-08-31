from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from scripts.g0 import verify_v12_performance as performance
from scripts.g0.verify_v12_performance import (
    DatasetCandidate,
    ProbeDefinition,
    ReadOnlyAudit,
    VerificationError,
    _assert_read_only_sql,
    _assert_scale_unchanged,
    _comparison_candidates,
    _identity,
    _measure_probe,
    _overall_status,
    _percentile,
)


class FakeResult:
    def __init__(self, row: Mapping[str, Any]) -> None:
        self._row = row

    def mappings(self) -> FakeResult:
        return self

    def one(self) -> Mapping[str, Any]:
        return self._row


class IdentityConnection:
    def __init__(
        self,
        *,
        database: str = "TMS_G0_DEV",
        revision: str = "sql2014_0023",
        version: str = "12.0.6449.1",
        edition: int = 3,
        banner: str = "Microsoft SQL Server 2014",
    ) -> None:
        self.row = {
            "database_name": database,
            "schema_revision": revision,
            "product_version": version,
            "engine_edition": edition,
            "version_banner": banner,
        }

    def execute(self, statement, parameters=None) -> FakeResult:
        del statement, parameters
        return FakeResult(self.row)


def _scale(**updates: float) -> dict[str, int | float]:
    result: dict[str, int | float] = {
        field: index for index, field in enumerate(performance._ROW_COUNT_FIELDS, 1)
    }
    result.update({"data_size_mb": 100.0, "log_size_mb": 20.0})
    result.update(updates)
    return result


def _candidate(
    dataset_id: int,
    *,
    stage: str,
    spec_set_id: int | None,
    unit_count: int = 100,
) -> DatasetCandidate:
    return DatasetCandidate(
        dataset_id=dataset_id,
        dataset_version_id=dataset_id * 10,
        version_no=1,
        test_stage=stage,
        spec_set_id=spec_set_id,
        unit_count=unit_count,
    )


def test_v12_contract_is_pinned_to_exact_development_database_and_schema() -> None:
    assert performance.EXPECTED_DATABASE == "TMS_G0_DEV"
    assert performance.EXPECTED_SCHEMA_REVISION == "sql2014_0023"
    assert _identity(IdentityConnection()) == {
        "database": "TMS_G0_DEV",
        "schema_revision": "sql2014_0023",
        "database_engine": "Microsoft SQL Server",
        "product_major": 12,
        "engine_edition": 3,
    }

    with pytest.raises(VerificationError) as database_error:
        _identity(IdentityConnection(database="TMS_PROD"))
    assert database_error.value.code == "DATABASE_IDENTITY_MISMATCH"

    with pytest.raises(VerificationError) as revision_error:
        _identity(IdentityConnection(revision="sql2014_0018"))
    assert revision_error.value.code == "SCHEMA_REVISION_MISMATCH"

    with pytest.raises(VerificationError) as engine_error:
        _identity(IdentityConnection(banner="PostgreSQL"))
    assert engine_error.value.code == "DATABASE_ENGINE_MISMATCH"


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("UPDATE dataset.dataset SET dataset_name='x'", "READ_ONLY_STATEMENT_REJECTED"),
        (
            "WITH scope AS (SELECT 1 AS id) DELETE FROM scope",
            "READ_ONLY_MUTATION_REJECTED",
        ),
        (
            "SELECT * INTO #copy FROM test.unit_result",
            "READ_ONLY_SELECT_INTO_REJECTED",
        ),
        ("SELECT 1; SELECT 2", "READ_ONLY_MULTIPLE_STATEMENTS"),
    ],
)
def test_read_only_guard_rejects_mutation_and_multi_statement(
    sql: str, code: str
) -> None:
    with pytest.raises(VerificationError) as error:
        _assert_read_only_sql(sql)
    assert error.value.code == code


def test_read_only_guard_ignores_tokens_inside_literals_and_comments() -> None:
    _assert_read_only_sql(
        "SELECT 'UPDATE dataset.dataset' AS sample /* DELETE is documentation */"
    )
    _assert_read_only_sql("WITH scope AS (SELECT 1 AS id) SELECT id FROM scope")


def test_guarded_connection_blocks_before_the_database_receives_mutation() -> None:
    class RawConnection:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, statement, parameters=None):
            del statement, parameters
            self.calls += 1
            return object()

    raw = RawConnection()
    audit = ReadOnlyAudit()
    guarded = performance._ReadOnlyConnection(raw, audit)

    with pytest.raises(VerificationError):
        guarded.execute(text("DELETE FROM test.measurement"))

    assert raw.calls == 0
    assert audit.statement_count == 0
    assert audit.blocked_statement_count == 1


def test_database_scale_drift_checks_rows_but_not_allocated_file_capacity() -> None:
    before = _scale()
    _assert_scale_unchanged(before, {**before, "data_size_mb": 120.0})

    with pytest.raises(VerificationError) as error:
        _assert_scale_unchanged(
            before,
            {**before, "measurements": int(before["measurements"]) + 1},
        )
    assert error.value.code == "READ_ONLY_SCALE_DRIFT"


def test_percentile_uses_documented_linear_interpolation() -> None:
    values = [50.0, 10.0, 40.0, 30.0, 20.0]
    assert _percentile(values, 0.50) == pytest.approx(30.0)
    assert _percentile(values, 0.95) == pytest.approx(48.0)

    with pytest.raises(ValueError):
        _percentile([], 0.5)
    with pytest.raises(ValueError):
        _percentile(values, 1.1)


def test_probe_records_cold_warm_sql_and_bytes_without_response_values() -> None:
    audit = ReadOnlyAudit()

    def operation() -> dict[str, str]:
        audit.statement_count += 2
        return {"lot": "SECRET-LOT", "login": "SECRET-USER"}

    definition = ProbeDefinition(
        name="catalog",
        warm_p95_limit_ms=60_000,
        operation=operation,
        observed_records=10,
        minimum_records=1,
        insufficient_reason="CATALOG_EMPTY",
    )
    evidence = _measure_probe(definition, audit, warm_runs=3)

    assert evidence["status"] == "PASS"
    assert evidence["cold_candidate"]["sql_statement_count"] == 2
    assert evidence["cold_candidate"]["response_bytes"] > 0
    assert evidence["warm"]["sample_count"] == 3
    assert evidence["warm"]["sql_statement_count"] == [2, 2, 2]
    assert all(value > 0 for value in evidence["warm"]["response_bytes"])
    serialized = json.dumps(evidence)
    assert "SECRET-LOT" not in serialized
    assert "SECRET-USER" not in serialized


def test_probe_applies_quality_cold_and_warm_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter(
        (
            0,
            6_000_000,
            10_000_000,
            11_000_000,
            20_000_000,
            21_000_000,
            30_000_000,
            31_000_000,
        )
    )
    monkeypatch.setattr(performance, "perf_counter_ns", lambda: next(ticks))
    definition = ProbeDefinition(
        name="quality_summary",
        warm_p95_limit_ms=3.0,
        cold_candidate_limit_ms=5.0,
        operation=lambda: {"status": "ok"},
        observed_records=1,
        minimum_records=1,
        insufficient_reason="QUALITY_EMPTY",
    )

    evidence = _measure_probe(definition, ReadOnlyAudit(), warm_runs=3)

    assert evidence["cold_candidate"]["elapsed_ms"] == pytest.approx(6.0)
    assert evidence["warm"]["p95_ms"] == pytest.approx(1.0)
    assert evidence["status"] == "FAIL"
    assert evidence["reason_code"] == "THRESHOLD_EXCEEDED"


def test_probe_skips_without_running_when_data_is_insufficient() -> None:
    called = False

    def operation() -> None:
        nonlocal called
        called = True

    evidence = _measure_probe(
        ProbeDefinition(
            name="compare_8_datasets_no_parameters",
            warm_p95_limit_ms=3_000,
            operation=operation,
            observed_records=7,
            minimum_records=8,
            insufficient_reason="EIGHT_COMPATIBLE_DATASETS_MISSING",
        ),
        ReadOnlyAudit(),
        warm_runs=3,
    )

    assert evidence["status"] == "SKIP"
    assert evidence["reason_code"] == "EIGHT_COMPATIBLE_DATASETS_MISSING"
    assert evidence["warm"]["sample_count"] == 0
    assert called is False


def test_probe_failure_keeps_exception_message_out_of_evidence() -> None:
    def operation() -> None:
        raise RuntimeError("server=SECRET;login=SECRET;lot=SECRET")

    evidence = _measure_probe(
        ProbeDefinition(
            name="detail",
            warm_p95_limit_ms=3_000,
            operation=operation,
            observed_records=1,
            minimum_records=1,
            insufficient_reason="DETAIL_EMPTY",
        ),
        ReadOnlyAudit(),
        warm_runs=3,
    )

    assert evidence["status"] == "FAIL"
    assert evidence["exception_type"] == "RuntimeError"
    assert "SECRET" not in json.dumps(evidence)


def test_comparison_selection_requires_eight_stage_and_spec_compatible_datasets() -> (
    None
):
    candidates = [
        _candidate(index, stage="CP", spec_set_id=77, unit_count=1_000 - index)
        for index in range(1, 9)
    ]
    candidates.extend(
        _candidate(index, stage="CP", spec_set_id=88) for index in range(20, 27)
    )
    candidates.extend(
        _candidate(index, stage="FT", spec_set_id=None) for index in range(40, 46)
    )

    selected = _comparison_candidates(candidates)

    assert len(selected) == 8
    assert {item.test_stage for item in selected} == {"CP"}
    assert {item.spec_set_id for item in selected} == {77}


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("PASS", "PASS"), "PASS"),
        (("PASS", "SKIP"), "SKIP"),
        (("PASS", "SKIP", "FAIL"), "FAIL"),
        ((), "SKIP"),
    ],
)
def test_overall_status_never_turns_skipped_coverage_into_pass(
    statuses: tuple[str, ...], expected: str
) -> None:
    probes = [{"status": status} for status in statuses]
    assert _overall_status(probes) == expected


def test_script_does_not_emit_database_url_or_business_response_values() -> None:
    source = Path(performance.__file__).read_text(encoding="utf-8")
    assert 'os.getenv("TMS_DATABASE_URL")' in source
    assert 'print(os.getenv("TMS_DATABASE_URL"))' not in source
    assert '"connection_string": os.getenv' not in source
    assert '"response_evidence": "serialized_byte_count_only"' in source
