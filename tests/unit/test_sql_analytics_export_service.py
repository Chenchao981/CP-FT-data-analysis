from __future__ import annotations

import json
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.core.errors import DomainError
from app.domain.analytics_exports import (
    CancelAnalyticsExportRequest,
    CreateAnalyticsExportRequest,
)
from app.domain.auth import Principal
from app.domain.saved_analyses import (
    SavedAnalysisRuleContext,
    saved_analysis_hashes,
    validate_analysis_presentation_config,
)
from app.infrastructure.analytics_export_files import AnalyticsExportPathPolicy
from app.infrastructure.sql_analytics_export_service import SqlAnalyticsExportService
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


def _principal(user_id: int = 10, *, admin: bool = False) -> Principal:
    return Principal(
        user_id=user_id,
        login_name=f"user-{user_id}",
        display_name=f"User {user_id}",
        roles=("SYSTEM_ADMIN",) if admin else ("FT_ENGINEER",),
        permissions=frozenset({"DATASET_READ", "EXPORT_DATA"}),
    )


def _request(
    dataset_ids: tuple[int, ...] = (1,),
    *,
    idempotency_key: str = "analytics-export-0001",
    export_format: str = "CSV",
    export_scope: str = "FILTERED_RESULT",
    template_code: str = "PARAMETER_DETAIL",
) -> CreateAnalyticsExportRequest:
    chart_config = {"show_spec_overlay": True, "correlation_min_abs": 0.2}
    if export_scope == "REPORT":
        report_configs = {
            "FT_QUALITY": {
                "contract_version": "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1",
                "section": "FT_QUALITY",
                "ft_quality": {
                    "analysis": "SYL_GROUPED_LIMIT",
                    "rule": {
                        "rule_code": "SYL_GROUPED_LIMIT",
                        "version_code": "V1",
                    },
                    "group_by": "DATASET",
                },
            },
            "ANALYTICS_OVERVIEW": {
                "contract_version": "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1",
                "section": "OVERVIEW",
                "overview": {"evaluations": []},
            },
            "PARAMETER_RELATIONSHIP": {
                "contract_version": "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1",
                "section": "PARAMETER_RELATIONSHIP",
                "parameter_relationship": {
                    "x_parameter": "IDSS",
                    "y_parameters": ["VTH"],
                    "analyses": ["SCATTER"],
                    "group_by": "DATASET",
                },
            },
        }
        chart_config["analysis"] = report_configs[template_code]
    return CreateAnalyticsExportRequest(
        datasets=[
            {"dataset_id": dataset_id, "version_no": 2} for dataset_id in dataset_ids
        ],
        filters={
            "lot_ids": ["LOT-B", "LOT-A"],
            "wafer_ids": [],
            "bin_codes": [],
            "overall_results": ["PASS", "FAIL"],
            "source_ids": [],
            "tester_ids": [],
            "program_versions": [],
            "test_conditions": [],
        },
        parameters=["VTH", "RDSON"],
        export_scope=export_scope,
        export_format=export_format,
        template_code=template_code,
        template_version="v1",
        rule_context=SavedAnalysisRuleContext(),
        chart_config=chart_config,
        display_config={"section": "parameter", "page": 1, "page_size": 50},
        artifact_ttl_hours=24,
        idempotency_key=idempotency_key,
        reason="Queue a fixed analytics context export",
    )


def _dataset(
    dataset_id: int,
    *,
    owner_user_id: int = 10,
    status: str = "PUBLISHED",
    is_current: bool = True,
    stage: str = "FT",
    spec_set_id: int | None = None,
) -> dict[str, Any]:
    return {
        "dataset_version_id": 1_000 + dataset_id,
        "dataset_id": dataset_id,
        "version_no": 2,
        "status": status,
        "is_current": is_current,
        "spec_set_id": spec_set_id,
        "test_stage": stage,
        "owner_user_id": owner_user_id,
        "supplier_id": 7,
        "product_id": 8,
        "business_domain": "ENGINEERING",
        "spec_version": "V1" if spec_set_id is not None else None,
    }


class _Result:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        scalar: Any = None,
    ) -> None:
        self.rows = rows or []
        self.scalar = scalar

    def mappings(self):
        return self

    def one_or_none(self):
        if not self.rows:
            return None
        assert len(self.rows) == 1
        return self.rows[0]

    def all(self):
        return list(self.rows)

    def scalar_one(self):
        assert self.scalar is not None
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar

    def scalars(self):
        if self.rows:
            return iter(row["export_job_id"] for row in self.rows)
        return iter(())


@dataclass
class _State:
    datasets: dict[tuple[int, int], dict[str, Any]] = field(default_factory=dict)
    jobs: dict[int, dict[str, Any]] = field(default_factory=dict)
    links: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    audits: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    statements: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    next_job_id: int = 1
    next_row_version: int = 1

    def row_version(self) -> bytes:
        value = self.next_row_version.to_bytes(8, "big")
        self.next_row_version += 1
        return value


class _Connection:
    def __init__(self, state: _State) -> None:
        self.state = state

    @staticmethod
    def _sql(statement) -> str:
        return " ".join(str(statement).split())

    def execute(self, statement, parameters=None):
        sql = self._sql(statement)
        params = dict(parameters or {})
        self.state.statements.append((sql, params))

        if sql.startswith("SELECT dv.dataset_version_id"):
            row = self.state.datasets.get(
                (int(params["dataset_id"]), int(params["version_no"]))
            )
            return _Result([dict(row)] if row is not None else [])

        if sql.startswith(
            "SELECT TOP (1) export_job_id,contract_version FROM delivery.export_job"
        ):
            existing = next(
                (
                    {
                        "export_job_id": job_id,
                        "contract_version": row["contract_version"],
                    }
                    for job_id, row in self.state.jobs.items()
                    if int(row["requested_by"]) == int(params["user_id"])
                    and row["idempotency_key"] == params["idempotency_key"]
                ),
                None,
            )
            return _Result([existing] if existing is not None else [])

        if sql.startswith("INSERT delivery.export_job("):
            job_id = self.state.next_job_id
            self.state.next_job_id += 1
            self.state.jobs[job_id] = {
                "export_job_id": job_id,
                "requested_by": int(params["requested_by"]),
                "dataset_version_id": int(params["dataset_version_id"]),
                "export_scope": params["export_scope"],
                "export_format": params["export_format"],
                "template_code": params["template_code"],
                "template_version": params["template_version"],
                "filter_json": params["filter_json"],
                "status": "QUEUED",
                "requested_at_utc": datetime(2026, 8, 31, 1, tzinfo=UTC),
                "started_at_utc": None,
                "finished_at_utc": None,
                "error_message": None,
                "contract_version": params["contract_version"],
                "filter_hash": params["filter_hash"],
                "context_hash": params["context_hash"],
                "rule_context_json": params["rule_context_json"],
                "idempotency_key": params["idempotency_key"],
                "exported_row_count": None,
                "row_version": self.state.row_version(),
            }
            self.state.links[job_id] = []
            return _Result(scalar=job_id)

        if sql.startswith("INSERT delivery.export_job_dataset("):
            self.state.links[int(params["export_job_id"])].append(
                (int(params["ordinal_no"]), int(params["dataset_version_id"]))
            )
            return _Result()

        if sql.startswith("INSERT governance.audit_log"):
            self.state.audits.append(params)
            return _Result()

        if sql.startswith("SELECT export_job_id,requested_by"):
            row = self.state.jobs.get(int(params["export_job_id"]))
            if row is not None and (
                row["contract_version"] != params["contract_version"]
                or not (
                    int(row["requested_by"]) == int(params["user_id"])
                    or bool(params["is_admin"])
                )
            ):
                row = None
            return _Result([dict(row)] if row is not None else [])

        if sql.startswith("SELECT ejd.dataset_version_id"):
            rows = []
            by_version = {
                int(row["dataset_version_id"]): row
                for row in self.state.datasets.values()
            }
            for ordinal, version_id in sorted(
                self.state.links[int(params["export_job_id"])]
            ):
                row = dict(by_version[version_id])
                row["ordinal_no"] = ordinal
                rows.append(row)
            return _Result(rows)

        if sql.startswith("UPDATE delivery.export_job SET status='CANCELLED'"):
            row = self.state.jobs[int(params["export_job_id"])]
            if (
                row["status"] != "QUEUED"
                or row["row_version"] != params["expected_row_version"]
            ):
                return _Result()
            row["status"] = "CANCELLED"
            row["finished_at_utc"] = datetime(2026, 8, 31, 2, tzinfo=UTC)
            row["row_version"] = self.state.row_version()
            return _Result(scalar=int(row["export_job_id"]))

        if sql.startswith("SELECT export_artifact_id"):
            return _Result(
                [
                    dict(row)
                    for row in self.state.artifacts.get(
                        int(params["export_job_id"]), []
                    )
                ]
            )

        if sql.startswith("SELECT COUNT_BIG(*) FROM delivery.export_job ej"):
            visible = [
                row
                for row in self.state.jobs.values()
                if row["contract_version"] == "ANALYTICS_EXPORT_V1"
                and (
                    int(row["requested_by"]) == int(params["user_id"])
                    or bool(params["is_admin"])
                )
            ]
            return _Result(scalar=len(visible))

        if sql.startswith("SELECT ej.export_job_id FROM delivery.export_job ej"):
            visible = [
                {"export_job_id": int(row["export_job_id"])}
                for row in self.state.jobs.values()
                if row["contract_version"] == "ANALYTICS_EXPORT_V1"
                and (
                    int(row["requested_by"]) == int(params["user_id"])
                    or bool(params["is_admin"])
                )
            ]
            visible.sort(key=lambda item: item["export_job_id"], reverse=True)
            offset = int(params["offset"])
            return _Result(visible[offset : offset + int(params["page_size"])])

        raise AssertionError(f"unexpected SQL: {sql}")


class _Context(AbstractContextManager[_Connection]):
    def __init__(self, state: _State) -> None:
        self.state = state

    def __enter__(self) -> _Connection:
        return _Connection(self.state)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _Engine:
    def __init__(self, state: _State) -> None:
        self.state = state

    def begin(self) -> _Context:
        return _Context(self.state)

    def connect(self) -> _Context:
        return _Context(self.state)


def _service(
    state: _State,
    path_policy: AnalyticsExportPathPolicy | None = None,
    approved_rule_resolver: Any | None = None,
) -> SqlAnalyticsExportService:
    def empty_rule_context(_connection, _dataset_rows, _parameters):
        return SavedAnalysisRuleContext()

    return SqlAnalyticsExportService(  # type: ignore[arg-type]
        _Engine(state),
        rule_context_resolver=empty_rule_context,
        approved_rule_resolver=approved_rule_resolver,
        path_policy=path_policy,
    )


def _overview_risk_request(
    *,
    dataset_ids: tuple[int, ...] = (1,),
    idempotency_key: str = "analytics-overview-risk-0001",
    rule_context: SavedAnalysisRuleContext | None = None,
) -> CreateAnalyticsExportRequest:
    request = _request(
        dataset_ids,
        idempotency_key=idempotency_key,
        export_scope="REPORT",
        template_code="ANALYTICS_OVERVIEW",
    )
    payload = request.model_dump(mode="json")
    payload["chart_config"]["analysis"]["overview"]["evaluations"] = [
        {
            "analysis": "CAPABILITY",
            "rule": {"rule_code": "OVERVIEW_CPK", "version_code": "V1"},
            "parameter": "VTH",
            "capability_method": "CPK_POOLED_WITHIN_RUN_V1",
        },
        {
            "analysis": "PAT_ROBUST_IQR",
            "rule": {"rule_code": "OVERVIEW_PAT", "version_code": "V2"},
            "parameter": "IDSS",
            "group_by": "DATASET",
        },
    ]
    if rule_context is not None:
        payload["rule_context"] = rule_context.model_dump(mode="json")
    return CreateAnalyticsExportRequest.model_validate(payload)


def test_create_queues_one_job_with_eight_exact_versions_and_no_fact_copy() -> None:
    state = _State(
        datasets={(dataset_id, 2): _dataset(dataset_id) for dataset_id in range(1, 9)}
    )
    service = _service(state)
    created = service.create(_request(tuple(range(1, 9))), _principal())

    assert created.status == "QUEUED"
    assert created.worker_contract_version == "ANALYTICS_EXPORT_WORKER_V1"
    assert [item.dataset_id for item in created.datasets] == list(range(1, 9))
    assert [item.ordinal_no for item in created.datasets] == list(range(1, 9))
    assert len(created.filter_hash) == len(created.context_hash) == 64
    assert len(created.presentation_hash) == 64
    assert created.chart_config["correlation_min_abs"] == 0.2
    assert len(state.jobs) == 1
    assert len(state.links[created.export_job_id]) == 8
    sql = "\n".join(statement for statement, _ in state.statements)
    assert "INSERT delivery.export_job(" in sql
    assert "INSERT delivery.export_job_dataset(" in sql
    assert "INSERT governance.audit_log" in sql
    assert "INSERT test." not in sql
    assert "INSERT delivery.export_artifact" not in sql
    assert "LOT-A" not in str(state.audits)
    fetched = service.get(created.export_job_id, _principal())
    page = service.list_page(_principal(), page=1, page_size=20)
    assert fetched.context_hash == created.context_hash
    assert page.total == 1
    assert page.items[0].export_job_id == created.export_job_id


def test_list_isolates_only_visible_integrity_blocked_jobs_without_rewriting_them() -> (
    None
):
    state = _State(
        datasets={
            (1, 2): _dataset(1),
            (2, 2): _dataset(2, owner_user_id=11),
        }
    )
    service = _service(state)
    good = service.create(
        _request(idempotency_key="analytics-export-good-0001"), _principal()
    )
    blocked = service.create(
        _request(idempotency_key="analytics-export-blocked-0001"), _principal()
    )
    hidden = service.create(
        _request((2,), idempotency_key="analytics-export-hidden-0001"),
        _principal(11),
    )
    blocked_before = str(state.jobs[blocked.export_job_id]["filter_json"])
    hidden_before = str(state.jobs[hidden.export_job_id]["filter_json"])
    blocked_payload = json.loads(blocked_before)
    blocked_payload.pop("chart_config")
    hidden_payload = json.loads(hidden_before)
    hidden_payload.pop("display_config")
    state.jobs[blocked.export_job_id]["filter_json"] = json.dumps(blocked_payload)
    state.jobs[hidden.export_job_id]["filter_json"] = json.dumps(hidden_payload)
    blocked_stored = str(state.jobs[blocked.export_job_id]["filter_json"])
    hidden_stored = str(state.jobs[hidden.export_job_id]["filter_json"])

    page = service.list_page(_principal(), page=1, page_size=20)

    assert page.total == 2  # All visible pagination slots, including the blocked one.
    assert [item.export_job_id for item in page.items] == [good.export_job_id]
    assert page.integrity_blocked_job_ids == (blocked.export_job_id,)
    assert page.integrity_blocked_count == 1
    assert hidden.export_job_id not in page.integrity_blocked_job_ids
    assert state.jobs[blocked.export_job_id]["filter_json"] == blocked_stored
    assert state.jobs[hidden.export_job_id]["filter_json"] == hidden_stored

    exact_operations = (
        lambda: service.get(blocked.export_job_id, _principal()),
        lambda: service.download_metadata(blocked.export_job_id, _principal()),
        lambda: service.resolve_download(blocked.export_job_id, 501, _principal()),
    )
    for operation in exact_operations:
        with pytest.raises(DomainError) as exact_access:
            operation()
        assert exact_access.value.code == "ANALYTICS_EXPORT_INTEGRITY_ERROR"
        assert exact_access.value.status_code == 409


def test_list_does_not_swallow_non_integrity_domain_errors() -> None:
    state = _State(datasets={(1, 2): _dataset(1)})
    service = _service(state)
    service.create(_request(), _principal())
    state.datasets[(1, 2)]["owner_user_id"] = 99

    with pytest.raises(DomainError) as revoked:
        service.list_page(_principal(), page=1, page_size=20)
    assert revoked.value.code == "ANALYTICS_EXPORT_ACCESS_REVOKED"


def test_current_page_create_freezes_typed_detail_filters_and_reconciles_replay() -> (
    None
):
    state = _State(datasets={(1, 2): _dataset(1)})
    service = _service(state)
    payload = _request().model_dump(mode="json")
    payload.update({"export_scope": "CURRENT_PAGE", "page": 2, "page_size": 25})
    payload["display_config"] = {
        "section": "detail",
        "page": 2,
        "page_size": 25,
        "focus_dataset_id": 1,
    }
    payload["chart_config"]["analysis_view_state"] = {
        "contract_version": "ANALYSIS_VIEW_STATE_V1",
        "components": {
            "detail": {
                "view": "LONG",
                "sortBy": "RESULT",
                "sortDirection": "DESC",
                "evaluation_filter": {
                    "evaluation_type": "PAT",
                    "evaluation_results": ["FAIL"],
                    "rule_code": "PAT_ROBUST_IQR",
                    "rule_version": "V2",
                },
                "measurement_filter": {
                    "parameter": "VTH",
                    "lower_bound": -2.0,
                    "upper_bound": 3.0,
                    "lower_inclusive": False,
                    "upper_inclusive": True,
                },
            }
        },
    }

    created = service.create(
        CreateAnalyticsExportRequest.model_validate(payload), _principal()
    )
    envelope = json.loads(state.jobs[created.export_job_id]["filter_json"])
    frozen = envelope["current_page_detail_state"]

    assert (
        frozen == payload["chart_config"]["analysis_view_state"]["components"]["detail"]
    )
    assert service.get(created.export_job_id, _principal()).page == 2

    # Pre-extension ANALYTICS_EXPORT_V1 envelopes remain replayable from chart_config.
    legacy_envelope = dict(envelope)
    legacy_envelope.pop("current_page_detail_state")
    state.jobs[created.export_job_id]["filter_json"] = json.dumps(legacy_envelope)
    assert service.get(created.export_job_id, _principal()).page_size == 25

    # The internal typed copy cannot diverge from the presentation hash-protected state.
    tampered_envelope = dict(envelope)
    tampered_envelope["current_page_detail_state"] = {
        **frozen,
        "measurement_filter": {
            **frozen["measurement_filter"],
            "lower_bound": -999.0,
        },
    }
    state.jobs[created.export_job_id]["filter_json"] = json.dumps(tampered_envelope)
    with pytest.raises(DomainError) as tampered:
        service.get(created.export_job_id, _principal())
    assert tampered.value.code == "ANALYTICS_EXPORT_INTEGRITY_ERROR"


def test_queue_rule_context_includes_parameters_pinned_by_report_config() -> None:
    state = _State(datasets={(1, 2): _dataset(1)})
    captured_parameters: list[tuple[str, ...]] = []

    def rule_context(_connection, _dataset_rows, context):
        captured_parameters.append(tuple(context.parameters))
        return SavedAnalysisRuleContext()

    service = SqlAnalyticsExportService(  # type: ignore[arg-type]
        _Engine(state),
        rule_context_resolver=rule_context,
        path_policy=AnalyticsExportPathPolicy(ROOT / "artifacts" / "unit-export"),
    )
    service.create(
        _request(
            export_scope="REPORT",
            template_code="PARAMETER_RELATIONSHIP",
        ),
        _principal(),
    )

    assert captured_parameters == [("IDSS", "RDSON", "VTH")]


def test_overview_export_validates_and_freezes_server_derived_exact_rules() -> None:
    state = _State(datasets={(1, 2): _dataset(1)})
    calls: list[dict[str, Any]] = []

    def approved_rule(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"approved_contract": "V1"}

    service = _service(state, approved_rule_resolver=approved_rule)
    created = service.create(_overview_risk_request(), _principal())

    assert created.rule_context.evaluation_rule_versions == [
        "RULE:OVERVIEW_CPK:V1",
        "RULE:OVERVIEW_PAT:V2",
    ]
    assert [
        (call["rule_code"], call["expected_algorithm_code"], call["parameter"])
        for call in calls
    ] == [
        ("OVERVIEW_CPK", "CPK_POOLED_WITHIN_RUN_V1", "VTH"),
        ("OVERVIEW_PAT", "PAT_SHARED_IQR_1_35_V1", "IDSS"),
    ]
    frozen = json.loads(state.jobs[created.export_job_id]["rule_context_json"])
    assert frozen["evaluation_rule_versions"] == [
        "RULE:OVERVIEW_CPK:V1",
        "RULE:OVERVIEW_PAT:V2",
    ]


def test_overview_export_rejects_missing_or_unapproved_exact_rule() -> None:
    state = _State(datasets={(1, 2): _dataset(1)})
    missing_payload = _overview_risk_request().model_dump(mode="json")
    missing_payload["chart_config"]["analysis"]["overview"]["evaluations"][0]["rule"][
        "version_code"
    ] = ""
    with pytest.raises(ValidationError, match="version_code"):
        CreateAnalyticsExportRequest.model_validate(missing_payload)

    def revoked(**_kwargs: Any) -> dict[str, Any]:
        raise DomainError("ANALYSIS_RULE_NOT_APPROVED", "approval was revoked", 409)

    with pytest.raises(DomainError) as unapproved:
        _service(state, approved_rule_resolver=revoked).create(
            _overview_risk_request(idempotency_key="overview-risk-revoked"),
            _principal(),
        )
    assert unapproved.value.code == "ANALYSIS_RULE_NOT_APPROVED"
    assert state.jobs == {}


def test_overview_export_rejects_algorithm_mismatch_and_client_rule_injection() -> None:
    state = _State(datasets={(1, 2): _dataset(1)})

    def wrong_algorithm(**kwargs: Any) -> dict[str, Any]:
        if kwargs["rule_code"] == "OVERVIEW_CPK":
            raise DomainError(
                "ANALYSIS_RULE_ALGORITHM_TYPE_MISMATCH",
                "wrong algorithm",
                409,
            )
        return {}

    with pytest.raises(DomainError) as mismatched:
        _service(state, approved_rule_resolver=wrong_algorithm).create(
            _overview_risk_request(idempotency_key="overview-risk-algorithm"),
            _principal(),
        )
    assert mismatched.value.code == "ANALYSIS_RULE_ALGORITHM_TYPE_MISMATCH"

    injected = SavedAnalysisRuleContext(
        evaluation_rule_versions=["RULE:CLIENT_INJECTED:V9"]
    )
    with pytest.raises(DomainError) as stale:
        _service(state, approved_rule_resolver=lambda **_kwargs: {}).create(
            _overview_risk_request(
                idempotency_key="overview-risk-injected", rule_context=injected
            ),
            _principal(),
        )
    assert stale.value.code == "ANALYTICS_EXPORT_RULE_CONTEXT_STALE"
    assert state.jobs == {}


def test_overview_export_rejects_inconsistent_rule_contract_across_scopes() -> None:
    first = _dataset(1)
    second = _dataset(2)
    second["supplier_id"] = 99
    state = _State(datasets={(1, 2): first, (2, 2): second})

    def scoped_contract(**kwargs: Any) -> dict[str, Any]:
        return {"scope": kwargs["supplier_id"]}

    with pytest.raises(DomainError) as captured:
        _service(state, approved_rule_resolver=scoped_contract).create(
            _overview_risk_request(
                dataset_ids=(1, 2), idempotency_key="overview-risk-scopes"
            ),
            _principal(),
        )

    assert captured.value.code == "ANALYSIS_RULE_CONTRACT_INVALID"
    assert state.jobs == {}


def test_overview_export_idempotency_uses_server_frozen_rule_context() -> None:
    state = _State(datasets={(1, 2): _dataset(1)})
    service = _service(state, approved_rule_resolver=lambda **_kwargs: {"v": 1})
    request = _overview_risk_request()
    shared_hashes = saved_analysis_hashes(request)

    first = service.create(request, _principal())
    replay = service.create(request, _principal())

    assert replay.export_job_id == first.export_job_id
    assert replay.idempotent_replay is True
    assert replay.presentation_hash == first.presentation_hash
    assert replay.context_hash == first.context_hash
    assert replay.rule_context == first.rule_context
    assert first.filter_hash == shared_hashes.filter_hash
    assert first.context_hash == shared_hashes.context_hash
    assert first.presentation_hash == validate_analysis_presentation_config(
        request.chart_config, request.display_config
    )


def test_idempotency_replays_only_the_exact_server_hashed_request() -> None:
    state = _State(datasets={(1, 2): _dataset(1)})
    service = _service(state)
    first = service.create(_request(), _principal())
    replay = service.create(_request(), _principal())

    assert replay.export_job_id == first.export_job_id
    assert replay.idempotent_replay is True
    assert len(state.jobs) == 1

    with pytest.raises(DomainError) as captured:
        service.create(_request(export_format="XLSX"), _principal())
    assert captured.value.code == "ANALYTICS_EXPORT_IDEMPOTENCY_CONFLICT"

    changed_presentation = _request().model_copy(
        update={"chart_config": {"show_spec_overlay": False}}
    )
    with pytest.raises(DomainError) as presentation_conflict:
        service.create(changed_presentation, _principal())
    assert presentation_conflict.value.code == "ANALYTICS_EXPORT_IDEMPOTENCY_CONFLICT"


def test_stored_presentation_hash_detects_filter_envelope_tampering() -> None:
    state = _State(datasets={(1, 2): _dataset(1)})
    service = _service(state)
    created = service.create(_request(), _principal())
    payload = json.loads(state.jobs[created.export_job_id]["filter_json"])
    payload["chart_config"]["correlation_min_abs"] = 0.9
    state.jobs[created.export_job_id]["filter_json"] = json.dumps(payload)

    with pytest.raises(DomainError) as tampered:
        service.get(created.export_job_id, _principal())
    assert tampered.value.code == "ANALYTICS_EXPORT_INTEGRITY_ERROR"


def test_legacy_delivery_jobs_are_isolated_from_the_analytics_export_contract() -> None:
    state = _State(datasets={(1, 2): _dataset(1)})
    service = _service(state)
    created = service.create(_request(), _principal())
    state.jobs[created.export_job_id]["contract_version"] = "LEGACY_EXPORT_V1"

    with pytest.raises(DomainError) as hidden:
        service.get(created.export_job_id, _principal())
    assert hidden.value.code == "ANALYTICS_EXPORT_NOT_FOUND"
    assert service.list_page(_principal(), page=1, page_size=20).total == 0

    with pytest.raises(DomainError) as conflict:
        service.create(_request(), _principal())
    assert conflict.value.code == "ANALYTICS_EXPORT_IDEMPOTENCY_CONFLICT"


def test_download_is_fail_closed_until_worker_and_cancel_is_optimistic() -> None:
    state = _State(datasets={(1, 2): _dataset(1)})
    service = _service(state)
    created = service.create(_request(), _principal())
    metadata = service.download_metadata(created.export_job_id, _principal())

    assert metadata.availability == "PENDING_GENERATION"
    assert metadata.download_enabled is False
    assert metadata.reason_code == "ANALYTICS_EXPORT_WORKER_REQUIRED"
    assert metadata.artifacts == ()

    with pytest.raises(DomainError) as stale:
        service.cancel(
            created.export_job_id,
            CancelAnalyticsExportRequest(
                confirmation="CANCEL",
                expected_row_version="FFFFFFFFFFFFFFFF",
                reason="Cancel before the Worker claims the job",
            ),
            _principal(),
        )
    assert stale.value.code == "ANALYTICS_EXPORT_WRITE_CONFLICT"

    cancelled = service.cancel(
        created.export_job_id,
        CancelAnalyticsExportRequest(
            confirmation="CANCEL",
            expected_row_version=created.row_version,
            reason="Cancel before the Worker claims the job",
        ),
        _principal(),
    )
    assert cancelled.status == "CANCELLED"
    with pytest.raises(DomainError) as unsafe:
        service.cancel(
            created.export_job_id,
            CancelAnalyticsExportRequest(
                confirmation="CANCEL",
                expected_row_version=cancelled.row_version,
                reason="Do not cancel a terminal export twice",
            ),
            _principal(),
        )
    assert unsafe.value.code == "ANALYTICS_EXPORT_CANCEL_UNSAFE"


def test_failed_worker_reason_is_explicit_but_bounded() -> None:
    state = _State(datasets={(1, 2): _dataset(1)})
    service = _service(state)
    created = service.create(_request(), _principal())
    state.jobs[created.export_job_id].update(
        {
            "status": "FAILED",
            "error_message": (
                "ANALYTICS_EXPORT_DEPENDENCY_UNAVAILABLE: "
                "PDF rendering dependency is unavailable"
            ),
        }
    )

    failed = service.get(created.export_job_id, _principal())
    assert failed.failure_code == "ANALYTICS_EXPORT_DEPENDENCY_UNAVAILABLE"
    assert failed.failure_message == "PDF rendering dependency is unavailable"


def test_download_rechecks_owner_ttl_managed_path_size_and_sha(tmp_path: Path) -> None:
    state = _State(datasets={(1, 2): _dataset(1)})
    policy = AnalyticsExportPathPolicy(tmp_path / "analytics-exports")
    service = _service(state, policy)
    created = service.create(_request(), _principal())
    root = policy.prepare_job_root(created.export_job_id)
    path = root / f"analytics-export-{created.export_job_id}.csv"
    path.write_bytes(b"dataset_id,unit_id\r\n1,11\r\n")
    identity = policy.identify(created.export_job_id, path)
    now = datetime.now(UTC)
    state.jobs[created.export_job_id].update(
        {
            "status": "SUCCESS",
            "started_at_utc": now,
            "finished_at_utc": now,
            "exported_row_count": 1,
        }
    )
    state.artifacts[created.export_job_id] = [
        {
            "export_artifact_id": 501,
            "file_name": identity.file_name,
            "mime_type": "text/csv; charset=utf-8",
            "storage_uri": str(identity.path),
            "file_size": identity.file_size,
            "sha256": identity.sha256,
            "created_at_utc": now,
            "expires_at_utc": now + timedelta(hours=24),
        }
    ]

    metadata = service.download_metadata(created.export_job_id, _principal())
    assert metadata.download_enabled is True
    assert metadata.reason_code == "ANALYTICS_EXPORT_READY"
    target = service.resolve_download(created.export_job_id, 501, _principal())
    assert target.path == identity.path
    assert target.file_name == identity.file_name

    with pytest.raises(DomainError) as other_owner:
        service.resolve_download(created.export_job_id, 501, _principal(11))
    assert other_owner.value.code == "ANALYTICS_EXPORT_NOT_FOUND"
    assert (
        service.resolve_download(
            created.export_job_id, 501, _principal(11, admin=True)
        ).path
        == identity.path
    )

    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(DomainError) as tampered:
        service.resolve_download(created.export_job_id, 501, _principal())
    assert tampered.value.code == "ANALYTICS_EXPORT_ARTIFACT_INTEGRITY_ERROR"

    state.artifacts[created.export_job_id][0].update(
        {
            "created_at_utc": now - timedelta(hours=2),
            "expires_at_utc": now - timedelta(hours=1),
        }
    )
    with pytest.raises(DomainError) as expired:
        service.resolve_download(created.export_job_id, 501, _principal())
    assert expired.value.status_code == 410
    assert expired.value.code == "ANALYTICS_EXPORT_ARTIFACT_EXPIRED"


def test_create_rejects_non_current_and_cross_owner_engineering_versions() -> None:
    state = _State(datasets={(1, 2): _dataset(1, is_current=False)})
    with pytest.raises(DomainError) as non_current:
        _service(state).create(_request(), _principal())
    assert non_current.value.code == "ANALYTICS_EXPORT_DATASET_NOT_CURRENT"

    state = _State(datasets={(1, 2): _dataset(1, owner_user_id=99)})
    with pytest.raises(DomainError) as forbidden:
        _service(state).create(_request(), _principal())
    assert forbidden.value.code == "ANALYTICS_EXPORT_DATASET_ACCESS_DENIED"


def test_service_rejects_registered_template_on_an_incompatible_dataset_stage() -> None:
    state = _State(datasets={(1, 2): _dataset(1, stage="CP", spec_set_id=50)})
    request = _request(
        export_scope="REPORT",
        export_format="PDF",
        template_code="FT_QUALITY",
    )
    with pytest.raises(DomainError) as incompatible:
        _service(state).create(request, _principal())
    assert incompatible.value.code == "ANALYTICS_EXPORT_TEMPLATE_INCOMPATIBLE"


def test_migrations_support_export_context_ttl_formats_and_one_to_eight_versions() -> (
    None
):
    base = (ROOT / "db/alembic/sql/0002_unified_workflow_sql2014.sql").read_text(
        encoding="utf-8-sig"
    )
    governance = (
        ROOT / "db/alembic/sql/0020_analytics_governance_sql2014.sql"
    ).read_text(encoding="utf-8-sig")
    service = (
        ROOT / "backend/app/infrastructure/sql_analytics_export_service.py"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE delivery.export_job" in base
    assert "CREATE TABLE delivery.export_artifact" in base
    assert "expires_at_utc datetime2(3) NULL" in base
    assert "'CSV','XLSX','PNG','HTML','PDF','BIN_TXT'" in base
    assert "delivery.export_job_dataset" in governance
    assert "ordinal_no BETWEEN 1 AND 8" in governance
    assert "filter_hash char(64)" in governance
    assert "context_hash char(64)" in governance
    assert "rule_context_json" in governance
    assert "UX_export_job_idempotency" in governance
    assert "test.measurement" not in service
    assert "INSERT delivery.export_artifact" not in service
