from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from scripts.g0 import verify_v13_parameter_analysis as verifier
from scripts.g0.verify_v13_parameter_analysis import (
    AnalysisCandidate,
    DatabaseSnapshot,
    FormalSpecCoverage,
    IndependentStatistics,
    ReadOnlyAudit,
    VerificationError,
    _analysis_request,
    _assert_capability_rule_gate,
    _assert_read_only_sql,
    _assert_snapshot_unchanged,
    _identity,
    _overall_status,
    _ReadOnlyConnection,
    _reconcile_response,
    _run_invocations,
)


class FakeResult:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = list(rows or [])

    def mappings(self) -> FakeResult:
        return self

    def one(self) -> dict[str, Any]:
        assert len(self.rows) == 1
        return self.rows[0]

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class IdentityConnection:
    def __init__(
        self,
        *,
        database: str = "TMS_G0_DEV",
        revision: str = "sql2014_0019",
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
        return FakeResult(rows=[self.row])


class RecordingConnection:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, statement, parameters=None) -> FakeResult:
        del parameters
        self.executed.append(str(statement))
        return FakeResult(rows=[{"value": 1}])


def _candidate(stage: str = "CP") -> AnalysisCandidate:
    return AnalysisCandidate(
        dataset_id=11 if stage == "CP" else 22,
        dataset_version_id=101 if stage == "CP" else 202,
        version_no=3,
        test_stage=stage,
        parameter_name="RDSON",
        measurement_count=9,
        numeric_count=7,
    )


def _independent() -> IndependentStatistics:
    return IndependentStatistics(
        row_count=9,
        numeric_count=7,
        status_counts={
            "MEASURED": 7,
            "OVER_RANGE": 0,
            "UNDER_RANGE": 0,
            "NOT_TESTED": 0,
            "MISSING": 2,
            "INVALID": 0,
            "NOT_APPLICABLE": 0,
        },
        minimum=1.0,
        maximum=100.0,
        average=17.285714285714285,
        sample_stddev=36.51222918412068,
        box_values={
            "minimum": 1.0,
            "q1": 2.5,
            "median": 4.0,
            "q3": 5.5,
            "maximum": 100.0,
            "lower_whisker": 1.0,
            "upper_whisker": 6.0,
        },
        outlier_count=1,
        histogram_total=7,
        histogram_non_empty_bin_count=3,
    )


def _capability_gate(**updates: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "status": "NOT_ELIGIBLE",
        "ppk_status": "NOT_REQUESTED",
        "cpk_status": "NOT_REQUESTED",
        "reason_codes": ("CAPABILITY_RULE_REQUIRED",),
        "spec_mode": None,
        "lsl": None,
        "usl": None,
        "sample_count": 7,
        "subgroup_count": 0,
        "overall_sigma": None,
        "within_sigma": None,
        "ppl": None,
        "ppu": None,
        "ppk": None,
        "cpl": None,
        "cpu": None,
        "cpk": None,
        "rule_code": None,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _response(candidate: AnalysisCandidate | None = None) -> SimpleNamespace:
    selected = candidate or _candidate()
    independent = _independent()
    parameter = SimpleNamespace(
        identity=SimpleNamespace(
            name=selected.parameter_name,
            limit_source="NOT_EVALUATED",
            spec_set_ids=(),
        ),
        status_counts=tuple(
            SimpleNamespace(status=status, count=count)
            for status, count in independent.status_counts.items()
        ),
        descriptive=SimpleNamespace(
            row_count=independent.row_count,
            numeric_count=independent.numeric_count,
            excluded_count=independent.row_count - independent.numeric_count,
            minimum=independent.minimum,
            maximum=independent.maximum,
            average=independent.average,
            sample_stddev=independent.sample_stddev,
        ),
        box_plot=SimpleNamespace(
            **dict(independent.box_values or {}),
            outlier_count=independent.outlier_count,
            method="TUKEY_1_5_IQR_PERCENTILE_CONT_LINEAR_V1",
        ),
        histogram=SimpleNamespace(
            requested_bin_count=20,
            method="EQUAL_WIDTH_FIXED_BINS_LAST_CLOSED_V1",
            bins=(
                SimpleNamespace(count=2),
                SimpleNamespace(count=4),
                SimpleNamespace(count=1),
            ),
        ),
        capability=_capability_gate(),
    )
    return SimpleNamespace(
        contract_version="PARAMETER_ANALYSIS_V1",
        group_by="DATASET",
        compatibility="SINGLE_DATASET",
        dataset_context=SimpleNamespace(
            resolved_datasets=(
                SimpleNamespace(
                    dataset_id=selected.dataset_id,
                    version_no=selected.version_no,
                ),
            ),
            test_stage=selected.test_stage,
            current_published_verified=True,
        ),
        filter_summary=SimpleNamespace(
            normalized_filters=SimpleNamespace(
                lot_ids=(),
                wafer_ids=(),
                bin_codes=(),
                overall_results=(),
                source_ids=(),
            ),
            filter_hash="a" * 64,
        ),
        rule_context=SimpleNamespace(
            spec_versions=(),
            bin_mapping_versions=(),
            evaluation_rule_versions=(
                "TUKEY_1_5_IQR_PERCENTILE_CONT_LINEAR_V1",
                "EQUAL_WIDTH_FIXED_BINS_LAST_CLOSED_V1",
            ),
            capability_rule_code=None,
            capability_rule_approval_status="NOT_REQUESTED",
        ),
        capabilities=(
            SimpleNamespace(code="DESCRIPTIVE", status="AVAILABLE", reason_code=None),
            SimpleNamespace(code="BOX_PLOT", status="AVAILABLE", reason_code=None),
            SimpleNamespace(code="HISTOGRAM", status="AVAILABLE", reason_code=None),
            SimpleNamespace(
                code="CAPABILITY",
                status="GATED",
                reason_code="CAPABILITY_RULE_REQUIRED",
            ),
        ),
        counts=SimpleNamespace(
            input_units=9,
            included_units=9,
            excluded_units=0,
            missing_measurements=2,
        ),
        sampling_summary=SimpleNamespace(
            sampled=False,
            method=None,
            original_points=0,
            returned_points=0,
            preserved_out_of_spec_points=0,
        ),
        warnings=("CAPABILITY_RULE_REQUIRED",),
        computed_at="2026-08-30T00:00:00+00:00",
        items=(
            SimpleNamespace(
                dataset_id=selected.dataset_id,
                version_no=selected.version_no,
                test_stage=selected.test_stage,
                filter_summary=SimpleNamespace(
                    matched_unit_count=9,
                    candidate_measurement_count=9,
                ),
                parameters=(parameter,),
            ),
        ),
    )


def test_read_only_guard_accepts_select_and_read_only_cte() -> None:
    _assert_read_only_sql("SELECT 1")
    _assert_read_only_sql(
        ";WITH values_cte AS (SELECT 1 AS value) SELECT value FROM values_cte"
    )
    _assert_read_only_sql("SELECT 'DROP TABLE hidden', 1 /* UPDATE hidden */")


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE test.measurement SET value_numeric=0",
        "SELECT 1; SELECT 2",
        "SELECT value_numeric INTO #copy FROM test.measurement",
        "EXEC sys.sp_who",
        "",
    ],
)
def test_read_only_guard_rejects_mutation_multi_statement_and_select_into(
    sql: str,
) -> None:
    with pytest.raises(VerificationError):
        _assert_read_only_sql(sql)


def test_read_only_connection_blocks_before_database_execution() -> None:
    connection = RecordingConnection()
    audit = ReadOnlyAudit()
    guarded = _ReadOnlyConnection(connection, audit)

    guarded.execute(text("SELECT 1"))
    with pytest.raises(VerificationError):
        guarded.execute(text("DELETE FROM test.measurement"))

    assert len(connection.executed) == 1
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
    with pytest.raises(VerificationError, match="TMS_G0_DEV"):
        _identity(IdentityConnection(database="TMS_PROD"))
    with pytest.raises(VerificationError, match="sql2014_0019"):
        _identity(IdentityConnection(revision="sql2014_0018"))
    with pytest.raises(VerificationError, match="Microsoft SQL Server"):
        _identity(IdentityConnection(banner="PostgreSQL"))


def _snapshot(*, current_digest: str = "b") -> DatabaseSnapshot:
    return DatabaseSnapshot(
        counts={
            "canonical": {"test_run": 1, "unit_result": 2, "measurement": 3},
            "current": {
                "dataset_version": 1,
                "test_run": 1,
                "unit_result": 2,
                "measurement": 3,
            },
        },
        canonical_group_count=1,
        canonical_summary_digest="a",
        current_group_count=1,
        current_summary_digest=current_digest,
    )


def test_snapshot_rejects_same_count_summary_membership_drift() -> None:
    _assert_snapshot_unchanged(_snapshot(), _snapshot())
    with pytest.raises(VerificationError) as exc:
        _assert_snapshot_unchanged(_snapshot(), _snapshot(current_digest="changed"))
    assert exc.value.code == "READ_ONLY_SNAPSHOT_DRIFT"


def test_analysis_request_contains_all_four_analyses_but_no_capability_rule() -> None:
    request = _analysis_request(_candidate())
    assert {item.value for item in request.analyses} == {
        "DESCRIPTIVE",
        "BOX_PLOT",
        "HISTOGRAM",
        "CAPABILITY",
    }
    assert request.histogram.bin_count == 20
    assert request.capability.rule_code is None


def test_technical_g0_service_explicitly_approves_only_fixed_chart_methods(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    engine = object()
    service = object()

    def fake_service_factory(received_engine, **kwargs):
        captured["engine"] = received_engine
        captured.update(kwargs)
        return service

    monkeypatch.setattr(verifier, "SqlDatasetService", fake_service_factory)

    assert verifier._technical_g0_dataset_service(engine) is service
    assert captured["engine"] is engine
    assert captured["approved_parameter_analysis_rule_codes"] == frozenset(
        {
            "TUKEY_1_5_IQR_PERCENTILE_CONT_LINEAR_V1",
            "EQUAL_WIDTH_FIXED_BINS_LAST_CLOSED_V1",
        }
    )


def test_capability_without_rule_must_be_strictly_not_requested() -> None:
    _assert_capability_rule_gate(_capability_gate(), numeric_count=7)
    with pytest.raises(VerificationError) as exc:
        _assert_capability_rule_gate(
            _capability_gate(ppk=1.23, ppk_status="ELIGIBLE"), numeric_count=7
        )
    assert exc.value.code == "CAPABILITY_RULE_GATE_MISMATCH"


def test_reconcile_checks_descriptive_box_histogram_and_capability_gate() -> None:
    evidence = _reconcile_response(_response(), _candidate(), _independent())
    assert evidence["status"] == "PASS"
    assert evidence["row_count"] == 9
    assert evidence["numeric_count"] == 7
    assert evidence["histogram_count_total"] == 7
    assert evidence["capability_rule_gate"] == {
        "status": "PASS",
        "reason_code": "CAPABILITY_RULE_REQUIRED",
        "ppk_status": "NOT_REQUESTED",
        "cpk_status": "NOT_REQUESTED",
        "all_sigma_and_indices_null": True,
    }
    assert "RDSON" not in json.dumps(evidence)


def test_reconcile_allows_dataset_spec_context_without_formal_capability_limits() -> (
    None
):
    response = _response()
    response.items[0].parameters[0].identity.spec_set_ids = (17,)

    evidence = _reconcile_response(response, _candidate(), _independent())

    assert evidence["status"] == "PASS"


def test_reconcile_fails_when_histogram_total_does_not_match_sql() -> None:
    response = _response()
    response.items[0].parameters[0].histogram.bins = (
        SimpleNamespace(count=2),
        SimpleNamespace(count=4),
    )
    with pytest.raises(VerificationError) as exc:
        _reconcile_response(response, _candidate(), _independent())
    assert exc.value.code == "ANALYSIS_HISTOGRAM_SQL_MISMATCH"


class CountingService:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls = 0

    def analyze_parameters(self, request) -> Any:
        assert request.capability.rule_code is None
        self.calls += 1
        return self.response


def test_invocations_default_shape_and_support_thirty_warm_runs() -> None:
    response = _response()
    service = CountingService(response)
    audit = ReadOnlyAudit(statement_count=4)

    returned, evidence = _run_invocations(
        service,
        _analysis_request(_candidate()),
        audit,
        warm_runs=30,
    )

    assert returned is response
    assert service.calls == 31
    assert evidence["status"] == "PASS"
    assert len(evidence["invocations"]) == 31
    assert evidence["invocations"][0]["phase"] == "cold_candidate"
    assert all(item["sql_statement_count"] == 0 for item in evidence["invocations"])
    assert all("elapsed_ms" in item for item in evidence["invocations"])
    assert evidence["response_summary_sha256"]


def test_formal_spec_absence_and_stage_absence_are_skip_not_pass(monkeypatch) -> None:
    candidate = _candidate()
    monkeypatch.setattr(verifier, "_formal_spec_rows", lambda connection, item: ())
    coverage = verifier._formal_spec_coverage(SimpleNamespace(), candidate)
    assert coverage == FormalSpecCoverage(
        status="SKIP",
        reason_code="FORMAL_RELEASED_SPEC_NOT_FOUND",
        signature_count=0,
        signature_digest=None,
    )
    assert _overall_status([{"status": "PASS"}, {"status": "SKIP"}]) == "SKIP"
    assert _overall_status([{"status": "FAIL"}, {"status": "SKIP"}]) == "FAIL"


def test_stage_selector_prefers_compatible_candidate_with_formal_spec(
    monkeypatch,
) -> None:
    cp_without_spec = _candidate("CP")
    cp_with_spec = AnalysisCandidate(
        dataset_id=12,
        dataset_version_id=102,
        version_no=1,
        test_stage="CP",
        parameter_name="VTH",
        measurement_count=8,
        numeric_count=8,
    )
    ft_with_spec = _candidate("FT")
    monkeypatch.setattr(
        verifier, "_candidate_identity_is_compatible", lambda connection, item: True
    )

    def coverage(connection, item):
        del connection
        if item is cp_without_spec:
            return FormalSpecCoverage("SKIP", "FORMAL_RELEASED_SPEC_NOT_FOUND", 0, None)
        return FormalSpecCoverage("PASS", "FORMAL_SPEC_AVAILABLE", 1, "digest")

    monkeypatch.setattr(verifier, "_formal_spec_coverage", coverage)
    selected = verifier._select_stage_candidates(
        SimpleNamespace(), (cp_without_spec, cp_with_spec, ft_with_spec)
    )
    assert selected["CP"] is not None and selected["CP"][0] is cp_with_spec
    assert selected["FT"] is not None and selected["FT"][0] is ft_with_spec


def test_warm_run_validation_accepts_thirty_and_rejects_out_of_range() -> None:
    class NeverEngine:
        @contextmanager
        def connect(self):
            raise AssertionError("invalid warm count must fail before database access")
            yield

    with pytest.raises(VerificationError) as low:
        verifier.verify(NeverEngine(), warm_runs=0)
    assert low.value.code == "WARM_RUN_COUNT_INVALID"
    with pytest.raises(VerificationError) as high:
        verifier.verify(NeverEngine(), warm_runs=101)
    assert high.value.code == "WARM_RUN_COUNT_INVALID"
    assert verifier._parser().parse_args([]).warm_runs == 5
    assert verifier._parser().parse_args(["--warm-runs", "30"]).warm_runs == 30


def test_source_and_public_evidence_do_not_emit_raw_identity_or_secret_values() -> None:
    source = Path(verifier.__file__).read_text(encoding="utf-8")
    assert 'capability={"rule_code"' not in source
    assert "CPK_POOLED_WITHIN_RUN_V1" not in source
    assert "CPK_POOLED_WITHIN_LOT_WAFER_V1" not in source

    candidate_public = verifier._public_candidate(_candidate())
    serialized = json.dumps(candidate_public, sort_keys=True)
    assert "RDSON" not in serialized
    assert '"dataset_id"' not in serialized
    assert '"dataset_version_id"' not in serialized
    assert "password" not in serialized.lower()
    assert "://" not in serialized
