from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from scripts.g0 import verify_v13_analytics_closure_performance as performance
from scripts.g0.verify_v13_analytics_closure_performance import (
    DatasetCandidate,
    InvocationResult,
    ParameterCoverage,
    ReadOnlyAudit,
    ResponseObservation,
    ScenarioDefinition,
    VerificationError,
    _aggregate_load,
    _assert_canonical_counts_unchanged,
    _assert_read_only_sql,
    _common_parameters,
    _common_relationship_parameters,
    _identity,
    _latency_statistic_label,
    _measure_scenario,
    _normalized_condition,
    _overall_status,
    _parameter_coverage,
    _percentile,
    _released_formal_spec_coverage,
    _response_observation,
    _scenario_definitions,
    _select_eight_candidates,
    _validate_run_controls,
)


class FakeResult:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def mappings(self) -> FakeResult:
        return self

    def one(self) -> dict[str, Any]:
        return self.row

    def all(self) -> list[dict[str, Any]]:
        return [self.row]


class IdentityConnection:
    def __init__(
        self,
        *,
        database: str = "TMS_G0_DEV",
        revision: str = "sql2014_0029",
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


def _candidate(
    dataset_id: int,
    *,
    stage: str = "FT",
    spec_set_id: int | None = None,
    measurements: int = 1_000,
) -> DatasetCandidate:
    return DatasetCandidate(
        dataset_id=dataset_id,
        dataset_version_id=dataset_id * 10,
        version_no=1,
        test_stage=stage,
        spec_set_id=spec_set_id,
        unit_count=100,
        measurement_count=measurements,
        wafer_count=2 if stage == "CP" else 0,
        coordinate_count=100 if stage == "CP" else 0,
    )


def _parameter(
    name: str,
    *,
    signature: tuple[object, ...] | None = None,
    count: int = 100,
    spec_versions: tuple[str, ...] = (),
) -> ParameterCoverage:
    return ParameterCoverage(
        name=name,
        signature=signature or ("STEP", 1, None, "V", None),
        minimum_measurement_count=count,
        total_measurement_count=count,
        spec_versions=spec_versions,
    )


def _formal_spec_row(
    candidate: DatasetCandidate,
    parameter: str,
    *,
    spec_set_id: int,
    version_code: str,
    spec_item_id: int | None = 1,
    lsl: float | None = 1.0,
    usl: float | None = 2.0,
    lower_operator: str | None = ">=",
    upper_operator: str | None = "<=",
) -> dict[str, Any]:
    program_version_id = 1_000 + candidate.dataset_id
    return {
        "dataset_version_id": candidate.dataset_version_id,
        "run_id": candidate.dataset_version_id,
        "test_stage": candidate.test_stage,
        "event_at_utc": None,
        "run_program_version_id": program_version_id,
        "item_program_version_id": program_version_id,
        "test_item_id": sum(ord(character) for character in parameter),
        "lot_id": f"LOT-{candidate.dataset_id}",
        "raw_item_name": parameter,
        "spec_binding_id": spec_set_id * 100 + candidate.dataset_id,
        "scope_priority": 100,
        "spec_set_id": spec_set_id,
        "version_code": version_code,
        "spec_item_id": spec_item_id,
        "unit_code": "V",
        "lsl": lsl,
        "usl": usl,
        "lower_operator": lower_operator,
        "upper_operator": upper_operator,
        "condition_json": None,
    }


def _observation(
    *,
    response_bytes: int = 100,
    observed: int = 20,
    returned: int = 10,
    original_points: int | None = 20,
    sampling_digest: str | None = "a" * 64,
) -> ResponseObservation:
    return ResponseObservation(
        response_bytes=response_bytes,
        observed_row_count=observed,
        returned_record_count=returned,
        sampling_original_points=original_points,
        sampling_returned_points=returned if original_points is not None else None,
        sampling_preserved_out_of_spec_points=(
            2 if original_points is not None else None
        ),
        sampling_digest_sha256=sampling_digest,
    )


def test_contract_is_pinned_to_exact_development_database_and_schema() -> None:
    assert _identity(IdentityConnection()) == {
        "database": "TMS_G0_DEV",
        "schema_revision": "sql2014_0029",
        "database_engine": "Microsoft SQL Server",
        "product_major": 12,
        "engine_edition": 3,
    }

    with pytest.raises(VerificationError) as database_error:
        _identity(IdentityConnection(database="TMS_PROD"))
    assert database_error.value.code == "DATABASE_IDENTITY_MISMATCH"

    with pytest.raises(VerificationError) as revision_error:
        _identity(IdentityConnection(revision="sql2014_0019"))
    assert revision_error.value.code == "SCHEMA_REVISION_MISMATCH"

    with pytest.raises(VerificationError) as engine_error:
        _identity(IdentityConnection(banner="PostgreSQL"))
    assert engine_error.value.code == "DATABASE_ENGINE_MISMATCH"


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        (
            "UPDATE test.unit_result SET overall_result='PASS'",
            "READ_ONLY_STATEMENT_REJECTED",
        ),
        (
            "WITH scope AS (SELECT 1 AS id) DELETE FROM scope",
            "READ_ONLY_MUTATION_REJECTED",
        ),
        ("SELECT * INTO #copy FROM test.measurement", "READ_ONLY_SELECT_INTO_REJECTED"),
        ("SELECT 1; SELECT 2", "READ_ONLY_MULTIPLE_STATEMENTS"),
    ],
)
def test_read_only_guard_rejects_all_mutating_shapes(sql: str, code: str) -> None:
    with pytest.raises(VerificationError) as error:
        _assert_read_only_sql(sql)
    assert error.value.code == code


def test_read_only_guard_blocks_before_raw_connection_and_counts_block() -> None:
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


def test_canonical_drift_is_never_accepted_as_read_only() -> None:
    before = {
        field: index
        for index, field in enumerate(performance._CANONICAL_COUNT_FIELDS, 1)
    }
    _assert_canonical_counts_unchanged(before, dict(before))

    with pytest.raises(VerificationError) as error:
        _assert_canonical_counts_unchanged(
            before, {**before, "measurements": before["measurements"] + 1}
        )
    assert error.value.code == "READ_ONLY_CANONICAL_DRIFT"


def test_condition_normalization_matches_exact_identity_contract() -> None:
    assert _normalized_condition(None) is None
    assert _normalized_condition('{"text":"  VDS   10 V "}') == "VDS 10 V"
    assert (
        _normalized_condition('{"bias2":" 2 V ","bias1":" 1 V "}')
        == '{"bias1":"1 V","bias2":"2 V"}'
    )
    assert _normalized_condition('["unsupported"]') is performance._INVALID_CONDITION
    assert _normalized_condition('{"text":"INVALID"}') == "INVALID"


def test_common_parameters_require_every_dataset_and_one_exact_signature() -> None:
    candidates = tuple(_candidate(index) for index in range(1, 4))
    coverage = {
        candidates[0].dataset_version_id: (
            _parameter("COMMON", count=100),
            _parameter("ONLY_ONE"),
        ),
        candidates[1].dataset_version_id: (_parameter("COMMON", count=80),),
        candidates[2].dataset_version_id: (_parameter("COMMON", count=60),),
    }

    common = _common_parameters(candidates, coverage)

    assert [item.name for item in common] == ["COMMON"]
    assert common[0].minimum_measurement_count == 60
    assert common[0].total_measurement_count == 240

    coverage[candidates[2].dataset_version_id] = (
        _parameter("COMMON", signature=("OTHER", 1, None, "V", None)),
    )
    assert _common_parameters(candidates, coverage) == ()


def test_parameter_discovery_matches_service_exact_identity_and_every_program() -> None:
    candidate = _candidate(1)
    identity_rows: list[dict[str, Any]] = []
    for program_version_id in (101, 102):
        identity_rows.extend(
            [
                {
                    "dataset_version_id": candidate.dataset_version_id,
                    "run_program_version_id": program_version_id,
                    "item_program_version_id": program_version_id,
                    "raw_item_name": "VTH1(V)",
                    "canonical_parameter_code": " VTH1(V) ",
                    "step_code": " vth1(v) ",
                    "sequence_no": 1,
                    "unit_code": " V ",
                    "condition_json": '{"text":" ID=250uA "}',
                },
                {
                    "dataset_version_id": candidate.dataset_version_id,
                    "run_program_version_id": program_version_id,
                    "item_program_version_id": program_version_id,
                    "raw_item_name": "INCOMPLETE_STEP",
                    "canonical_parameter_code": None,
                    "step_code": None,
                    "sequence_no": 2,
                    "unit_code": "V",
                    "condition_json": None,
                },
                {
                    "dataset_version_id": candidate.dataset_version_id,
                    "run_program_version_id": program_version_id,
                    "item_program_version_id": program_version_id,
                    "raw_item_name": "AMBIGUOUS_UNMEASURED",
                    "canonical_parameter_code": None,
                    "step_code": "AMBIGUOUS",
                    "sequence_no": 4,
                    "unit_code": "V",
                    "condition_json": None,
                },
            ]
        )
    identity_rows.extend(
        [
            {
                "dataset_version_id": candidate.dataset_version_id,
                "run_program_version_id": 102,
                "item_program_version_id": 102,
                "raw_item_name": "AMBIGUOUS_UNMEASURED",
                "canonical_parameter_code": None,
                "step_code": "OTHER",
                "sequence_no": 4,
                "unit_code": "V",
                "condition_json": None,
            },
            {
                "dataset_version_id": candidate.dataset_version_id,
                "run_program_version_id": 101,
                "item_program_version_id": 101,
                "raw_item_name": "ONE_PROGRAM_ONLY",
                "canonical_parameter_code": None,
                "step_code": "ONE",
                "sequence_no": 3,
                "unit_code": "V",
                "condition_json": None,
            },
        ]
    )
    count_rows = [
        {
            "dataset_version_id": candidate.dataset_version_id,
            "raw_item_name": name,
            "measurement_count": count,
        }
        for name, count in (
            ("VTH1(V)", 50),
            ("INCOMPLETE_STEP", 50),
            ("AMBIGUOUS_UNMEASURED", 50),
            ("ONE_PROGRAM_ONLY", 20),
        )
    ]

    class RowsResult:
        def __init__(self, values: list[dict[str, Any]]) -> None:
            self.values = values

        def mappings(self):
            return self

        def all(self):
            return self.values

    class RowsConnection:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, statement, parameters=None):
            self.calls += 1
            assert parameters == {
                "dataset_version_ids": (candidate.dataset_version_id,)
            }
            if self.calls == 1:
                assert "SELECT DISTINCT" in str(statement)
                assert "LEFT JOIN mdm.test_item_definition" in str(statement)
                return RowsResult(identity_rows)
            assert "program_lsl" not in str(statement)
            assert "tid.program_version_id=tr.program_version_id" in str(statement)
            return RowsResult(count_rows)

    discovered = _parameter_coverage(RowsConnection(), (candidate,))

    assert discovered[candidate.dataset_version_id] == (
        _parameter(
            "VTH1(V)",
            signature=("VTH1(V)", 1, "VTH1(V)", "V", "ID=250uA"),
            count=50,
        ),
    )


def test_released_formal_spec_discovery_uses_live_resolver_and_keeps_identity() -> None:
    candidates = (_candidate(1), _candidate(2))
    identity = ("STEP", 1, None, "V", None)
    coverage = {
        candidate.dataset_version_id: (
            _parameter("GOOD", signature=identity, count=3),
            _parameter("PARTIAL", signature=identity, count=3),
        )
        for candidate in candidates
    }
    rows: list[dict[str, Any]] = []
    for candidate, spec_set_id, version_code in zip(
        candidates, (77, 88), ("V1", "V2"), strict=True
    ):
        rows.extend(
            [
                _formal_spec_row(
                    candidate,
                    "GOOD",
                    spec_set_id=spec_set_id,
                    version_code=version_code,
                ),
                _formal_spec_row(
                    candidate,
                    "PARTIAL",
                    spec_set_id=spec_set_id,
                    version_code=version_code,
                    spec_item_id=None,
                ),
            ]
        )

    class RowsResult:
        def mappings(self):
            return self

        def all(self):
            return rows

    class RowsConnection:
        def execute(self, statement, parameters=None):
            sql = str(statement)
            assert "measurement_evaluation" not in sql
            assert "EXISTS(SELECT 1 FROM test.unit_result" in sql
            assert "sb.active" not in sql
            assert parameters == {
                "dataset_version_ids": tuple(
                    item.dataset_version_id for item in candidates
                )
            }
            return RowsResult()

    discovered = _released_formal_spec_coverage(RowsConnection(), candidates, coverage)
    common = _common_parameters(candidates, discovered)

    assert [item.name for item in discovered[candidates[0].dataset_version_id]] == [
        "GOOD"
    ]
    assert discovered[candidates[0].dataset_version_id][0].spec_versions == (
        "SPEC:77:V1",
    )
    assert discovered[candidates[1].dataset_version_id][0].spec_versions == (
        "SPEC:88:V2",
    )
    assert [item.name for item in common] == ["GOOD"]
    assert common[0].spec_versions == ("SPEC:77:V1", "SPEC:88:V2")
    evidence = performance._formal_spec_identity_evidence(
        candidates, discovered, {"GOOD"}
    )
    assert evidence[0]["parameters"] == [
        {"parameter": "GOOD", "spec_versions": ["SPEC:77:V1"]}
    ]
    assert evidence[1]["parameters"] == [
        {"parameter": "GOOD", "spec_versions": ["SPEC:88:V2"]}
    ]


@pytest.mark.parametrize(
    "second_signature",
    [
        ("V", None, 1.0, 2.1, ">=", "<="),
        ("V", None, 1.0, 2.0, ">=", "<"),
    ],
    ids=("numeric_limit_differs", "operator_differs"),
)
def test_relationship_compatibility_rejects_formal_value_or_operator_difference(
    second_signature: tuple[object, ...],
) -> None:
    candidates = (_candidate(1), _candidate(2))
    exact = {
        candidate.dataset_version_id: (_parameter("P1"),) for candidate in candidates
    }
    formal = {
        candidates[0].dataset_version_id: (
            _parameter("P1", signature=("V", None, 1.0, 2.0, ">=", "<=")),
        ),
        candidates[1].dataset_version_id: (
            _parameter("P1", signature=second_signature),
        ),
    }

    assert _common_relationship_parameters(candidates, exact, formal) == ()


def test_eight_dataset_selection_finds_105_to_112_across_spec_identities() -> None:
    blockers = (
        _candidate(1, measurements=99_000),
        _candidate(2, measurements=98_000),
    )
    expected = tuple(
        _candidate(dataset_id, measurements=90_000 - dataset_id)
        for dataset_id in range(105, 113)
    )
    candidates = (*blockers, *expected)
    coverage = {
        candidate.dataset_version_id: (_parameter("VTH1"), _parameter("VTH2"))
        for candidate in candidates
    }
    formal: dict[int, tuple[ParameterCoverage, ...]] = {}
    for candidate in blockers:
        formal[candidate.dataset_version_id] = (
            _parameter("VTH1", signature=("V", None, 1.4, 2.0, ">=", "<=")),
        )
    for candidate in expected:
        identity = "SPEC:15:OLD" if candidate.dataset_id <= 108 else "SPEC:16:NEW"
        formal[candidate.dataset_version_id] = (
            _parameter(
                "VTH1",
                signature=("V", None, 1.4, 2.0, ">=", "<="),
                spec_versions=(identity,),
            ),
            _parameter(
                "VTH2",
                signature=("V", None, 1.4, 2.1, ">=", "<="),
                spec_versions=(identity,),
            ),
        )

    selected = _select_eight_candidates(candidates, coverage, formal)
    common = _common_relationship_parameters(selected, coverage, formal)

    assert [item.dataset_id for item in selected] == list(range(105, 113))
    assert [item.name for item in common] == ["VTH1", "VTH2"]
    assert common[0].spec_versions == ("SPEC:15:OLD", "SPEC:16:NEW")


def test_eight_dataset_selection_never_mixes_stage_or_uses_spec_id_as_key() -> None:
    cp_good = tuple(
        _candidate(index, stage="CP", spec_set_id=77, measurements=2_000 - index)
        for index in range(1, 9)
    )
    cp_other = tuple(
        _candidate(index, stage="CP", spec_set_id=88, measurements=5_000)
        for index in range(20, 27)
    )
    ft = tuple(_candidate(index, measurements=10_000) for index in range(40, 48))
    coverage: dict[int, tuple[ParameterCoverage, ...]] = {}
    for candidate in (*cp_good, *cp_other):
        coverage[candidate.dataset_version_id] = tuple(
            _parameter(f"P{parameter}") for parameter in range(1, 6)
        )
    for candidate in ft:
        coverage[candidate.dataset_version_id] = (_parameter("ONLY_ONE"),)

    selected = _select_eight_candidates((*cp_good, *cp_other, *ft), coverage)

    assert len(selected) == 8
    assert {item.test_stage for item in selected} == {"CP"}
    assert {item.spec_set_id for item in selected} == {77, 88}


def test_scenario_contract_uses_baseline_detail_and_two_parameter_scatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cp = _candidate(90, stage="CP", spec_set_id=77)
    eight = tuple(_candidate(index) for index in range(1, 9))
    candidates = (cp, *eight)
    coverage = {
        candidate.dataset_version_id: tuple(
            _parameter(f"P{number}", count=100 - number) for number in range(1, 6)
        )
        for candidate in candidates
    }
    formal_spec_coverage = {
        candidate.dataset_version_id: (
            _parameter("P1", signature=("V", None, 1.0, 2.0, ">=", "<=")),
            _parameter("P2", signature=("V", None, 1.0, 2.0, ">=", "<=")),
        )
        for candidate in eight
    }
    captured: dict[str, list[Any]] = {"detail": [], "relationship": [], "wafer": []}

    class AnalyticsService:
        def __init__(self, engine) -> None:
            del engine

        def detail(self, request):
            captured["detail"].append(request)
            return request

        def overview(self, request):
            return request

    class RelationshipService:
        def __init__(self, engine) -> None:
            del engine

        def relationship(self, request):
            captured["relationship"].append(request)
            return request

    class WaferService:
        def __init__(self, engine) -> None:
            del engine

        def summarize(self, request):
            captured["wafer"].append(request)
            return request

    monkeypatch.setattr(performance, "SqlAnalyticsService", AnalyticsService)
    monkeypatch.setattr(
        performance, "SqlParameterRelationshipService", RelationshipService
    )
    monkeypatch.setattr(performance, "SqlWaferSummaryService", WaferService)

    definitions = _scenario_definitions(
        object(),
        candidates,
        coverage,
        formal_spec_coverage,
        eight,
        (performance.WaferScope(cp.dataset_id, "LOT", "W1", 100),),
        (),
    )
    by_name = {item.name: item for item in definitions}
    by_name["single_dataset_detail_200"].operation()
    by_name["eight_dataset_detail_200"].operation()
    by_name["single_dataset_parameter_relationship"].operation()
    by_name["eight_dataset_parameter_relationship"].operation()
    by_name["wafer_summary_page_200_up_to_5_parameters"].operation()

    assert [request.parameters for request in captured["detail"]] == [[], []]
    for request in captured["relationship"]:
        assert len(request.y_parameters) == 1
        assert request.analyses == [performance.ParameterRelationshipAnalysis.SCATTER]
    assert by_name["eight_dataset_parameter_relationship"].coverage_observed == 2
    assert by_name["wafer_summary_page_200_up_to_5_parameters"].coverage_observed == 2


def test_eight_dataset_relationship_skips_without_two_formal_spec_parameters() -> None:
    eight = tuple(_candidate(index) for index in range(1, 9))
    coverage = {
        candidate.dataset_version_id: tuple(
            _parameter(f"P{number}") for number in range(1, 6)
        )
        for candidate in eight
    }
    only_one_formal = {
        candidate.dataset_version_id: (
            _parameter("P1", signature=("V", None, 1.0, 2.0, ">=", "<=")),
        )
        for candidate in eight
    }
    definitions = _scenario_definitions(
        object(), eight, coverage, only_one_formal, eight, (), ()
    )
    relationship = next(
        item
        for item in definitions
        if item.name == "eight_dataset_parameter_relationship"
    )

    evidence = _measure_scenario(
        relationship, warmup=0, iterations=1, concurrencies=(1,)
    )

    assert evidence["status"] == "SKIP"
    assert (
        evidence["reason_code"]
        == "TWO_COMMON_FORMAL_SPEC_COMPATIBLE_PARAMETERS_MISSING"
    )
    assert performance._verification_exit_code("SKIP", smoke=False) == 1


def test_percentile_and_run_controls_are_frozen_for_formal_acceptance() -> None:
    assert _percentile([50.0, 10.0, 40.0, 30.0, 20.0], 0.95) == pytest.approx(48.0)
    assert _validate_run_controls(2, 30, (1, 5, 5)) == (1, 5)
    assert _validate_run_controls(0, 2, (1,), smoke=True) == (1,)

    with pytest.raises(VerificationError) as iterations_error:
        _validate_run_controls(2, 29, (1, 5))
    assert iterations_error.value.code == "ITERATIONS_INVALID"

    with pytest.raises(VerificationError) as concurrency_error:
        _validate_run_controls(2, 30, (2,))
    assert concurrency_error.value.code == "CONCURRENCY_INVALID"


def test_smoke_latency_label_never_claims_formal_p95() -> None:
    assert (
        _latency_statistic_label(smoke=True, iterations=1)
        == "SINGLE_OBSERVATION_NOT_FORMAL_P95"
    )
    assert (
        _latency_statistic_label(smoke=True, iterations=2)
        == "SMOKE_SAMPLE_PERCENTILE_NOT_FORMAL_P95"
    )
    assert _latency_statistic_label(smoke=False, iterations=30) == "FORMAL_P95"


def test_response_observation_reports_counts_and_sampling_without_values() -> None:
    response_type = type("ParameterRelationshipResult", (), {})
    response = response_type()
    response.sampling_summary = SimpleNamespace(
        original_points=100,
        returned_points=20,
        preserved_out_of_spec_points=3,
    )
    point = SimpleNamespace(
        drilldown_key="UNIT:999",
        x_parameter="SECRET-X",
        y_parameter="SECRET-Y",
    )
    response.items = (
        SimpleNamespace(scatter_points=(point,), trend_points=(), correlations=()),
    )
    response.model_dump = lambda mode: {
        "mode": mode,
        "sampling": {"original": 100, "returned": 20},
    }

    observation = _response_observation(response)

    assert observation.observed_row_count == 100
    assert observation.returned_record_count == 1
    assert observation.sampling_original_points == 100
    assert len(observation.sampling_digest_sha256 or "") == 64
    assert observation.response_bytes > 0


def test_load_aggregate_includes_error_rate_sql_counts_and_sampling_stability() -> None:
    results = [
        InvocationResult(10.0, 2, _observation(), None, None),
        InvocationResult(20.0, 3, _observation(), None, None),
        InvocationResult(30.0, 1, None, "ANALYSIS_FAILED", "DomainError"),
    ]

    evidence = _aggregate_load(results, 60.0, concurrency=5)

    assert evidence["request_count"] == 3
    assert evidence["success_count"] == 2
    assert evidence["error_rate"] == pytest.approx(1 / 3, abs=1e-6)
    assert evidence["request_latency_ms"] == {
        "p50": 20.0,
        "p95": 29.0,
        "max": 30.0,
    }
    assert evidence["sql_statement_count"]["total"] == 6
    assert evidence["sampling"]["stable"] is True
    assert evidence["errors"] == [
        {
            "error_code": "ANALYSIS_FAILED",
            "exception_type": "DomainError",
            "count": 1,
        }
    ]


def test_scenario_skips_missing_coverage_without_calling_service() -> None:
    called = False

    def operation() -> None:
        nonlocal called
        called = True

    evidence = _measure_scenario(
        ScenarioDefinition(
            name="eight_dataset_overview",
            operation=operation,
            p95_limit_ms=3_000,
            coverage_observed=7,
            coverage_required=8,
            coverage_reason="EIGHT_SAME_STAGE_COMPATIBLE_DATASETS_MISSING",
        ),
        warmup=0,
        iterations=1,
        concurrencies=(1,),
    )

    assert evidence["status"] == "SKIP"
    assert evidence["reason_code"] == "EIGHT_SAME_STAGE_COMPATIBLE_DATASETS_MISSING"
    assert called is False


def test_large_scatter_cannot_pass_when_actual_pair_is_not_large() -> None:
    response_type = type("ParameterRelationshipResult", (), {})

    def operation():
        response = response_type()
        response.sampling_summary = SimpleNamespace(
            original_points=10,
            returned_points=10,
            preserved_out_of_spec_points=1,
        )
        response.items = (
            SimpleNamespace(scatter_points=(), trend_points=(), correlations=()),
        )
        response.model_dump = lambda mode: {"mode": mode, "original": 10}
        return response

    evidence = _measure_scenario(
        ScenarioDefinition(
            name="single_parameter_large_scatter",
            operation=operation,
            p95_limit_ms=5_000,
            coverage_observed=11,
            coverage_required=11,
            coverage_reason="LARGE_SCATTER_PARAMETER_COVERAGE_MISSING",
            stable_sampling_required=True,
            minimum_original_points=11,
        ),
        warmup=0,
        iterations=1,
        concurrencies=(1,),
    )

    assert evidence["status"] == "SKIP"
    assert evidence["reason_code"] == "LARGE_SCATTER_PAIR_COVERAGE_MISSING"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("PASS", "PASS"), "PASS"),
        (("PASS", "SKIP"), "SKIP"),
        (("PASS", "SKIP", "FAIL"), "FAIL"),
        ((), "SKIP"),
    ],
)
def test_overall_status_never_turns_coverage_skip_into_pass(
    statuses: tuple[str, ...], expected: str
) -> None:
    assert _overall_status([{"status": status} for status in statuses]) == expected


@pytest.mark.parametrize(
    ("verification", "smoke", "expected"),
    [
        ("PASS", False, 0),
        ("SKIP", False, 1),
        ("FAIL", False, 1),
        ("SKIP", True, 0),
        ("FAIL", True, 1),
        ("UNKNOWN", False, 1),
    ],
)
def test_verification_exit_code_fails_closed_for_formal_skip(
    verification: str, smoke: bool, expected: int
) -> None:
    assert performance._verification_exit_code(verification, smoke=smoke) == expected


def test_script_never_emits_database_url_or_business_response_values() -> None:
    source = Path(performance.__file__).read_text(encoding="utf-8")
    assert 'os.getenv("TMS_DATABASE_URL")' in source
    assert 'print(os.getenv("TMS_DATABASE_URL"))' not in source
    assert '"connection_string": os.getenv' not in source
    assert '"response_body"' in source
    assert "response_value" not in json.dumps(
        _aggregate_load(
            [InvocationResult(1.0, 0, _observation(), None, None)],
            1.0,
            concurrency=1,
        )
    )
