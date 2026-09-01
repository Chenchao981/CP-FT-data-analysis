from __future__ import annotations

import copy
import json
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

import pytest
from app.core.errors import DomainError
from app.domain.analytics import AnalyticsContextRequest
from app.domain.auth import Principal
from app.domain.saved_analyses import (
    CreateSavedAnalysisRequest,
    CreateSavedAnalysisRevisionRequest,
    DeleteSavedAnalysisRequest,
    SavedAnalysisRuleContext,
    SavedAnalysisState,
    saved_analysis_hashes,
)
from app.infrastructure.sql_analytics_service import _hashes as analytics_hashes
from app.infrastructure.sql_saved_analysis_service import SqlSavedAnalysisService
from pydantic import ValidationError


def _principal(
    user_id: int,
    *,
    admin: bool = False,
    permissions: frozenset[str] = frozenset({"DATASET_READ", "ANALYSIS_RUN"}),
) -> Principal:
    return Principal(
        user_id=user_id,
        login_name=f"user-{user_id}",
        display_name=f"User {user_id}",
        roles=("SYSTEM_ADMIN",) if admin else ("ENGINEER",),
        permissions=permissions,
    )


def _create_request(
    dataset_ids: tuple[int, ...] = (1,),
    *,
    rule_context: SavedAnalysisRuleContext | None = None,
) -> CreateSavedAnalysisRequest:
    return CreateSavedAnalysisRequest(
        analysis_name="Saved parameter view",
        change_reason="Freeze an approved analytics context",
        contract_version="SAVED_ANALYSIS_V1",
        datasets=[
            {"dataset_id": dataset_id, "version_no": 1} for dataset_id in dataset_ids
        ],
        filters={
            "lot_ids": ["SYNTHETIC-A"],
            "wafer_ids": [],
            "bin_codes": [],
            "overall_results": ["PASS", "FAIL"],
            "source_ids": [],
            "tester_ids": [],
            "program_versions": [],
            "test_conditions": [],
        },
        parameters=["PARAM_B", "PARAM_A"],
        rule_context=rule_context or SavedAnalysisRuleContext(),
        chart_config={"analysis_view_state": _empty_analysis_view_state()},
        display_config={"max_points": 10_000, "legend": True},
    )


def _revision_request(
    row_version: str,
    dataset_ids: tuple[int, ...] = (1,),
) -> CreateSavedAnalysisRevisionRequest:
    base = _create_request(dataset_ids).model_dump(mode="json")
    base.pop("analysis_name")
    base.pop("change_reason")
    base.update(
        {
            "expected_row_version": row_version,
            "analysis_name": "Saved parameter view revision",
            "change_reason": "Revise the fixed display configuration",
            "display_config": {"max_points": 8_000, "legend": False},
        }
    )
    return CreateSavedAnalysisRevisionRequest.model_validate(base)


def _pat_analysis_view_state(
    *, rule_code: str = "OVERVIEW_PAT", version_code: str = "V1"
) -> dict[str, Any]:
    empty_rule = {"ruleCode": "", "versionCode": ""}
    return {
        "contract_version": "ANALYSIS_VIEW_STATE_V1",
        "components": {
            "overviewRisk": {
                "analyses": ["PAT_ROBUST_IQR"],
                "parameter": "PARAM_A",
                "groupBy": "DATASET",
                "capability": {
                    "method": "CPK_POOLED_WITHIN_RUN_V1",
                    **empty_rule,
                },
                "pat": {"ruleCode": rule_code, "versionCode": version_code},
                "spc": empty_rule,
                "margin": empty_rule,
                "sbl": {**empty_rule, "binType": "CP_BIN"},
                "syl": empty_rule,
            },
            "detail": {
                "view": "WIDE",
                "sortBy": "UNIT_SEQUENCE",
                "sortDirection": "ASC",
            },
            "parameterAnalysis": {
                "groupBy": "DATASET",
                "analyses": ["DESCRIPTIVE"],
                "boxPlot": empty_rule,
                "histogram": empty_rule,
                "normalFit": empty_rule,
                "capability": {
                    "method": "CPK_POOLED_WITHIN_RUN_V1",
                    **empty_rule,
                },
                "boxParameter": "",
                "histogramDataset": "",
                "histogramParameter": "",
                "normalFitDataset": "",
                "normalFitParameter": "",
            },
            "parameterRelationship": {
                "xParameter": "",
                "yParameters": [],
                "analyses": ["SCATTER"],
                "groupBy": "DATASET",
                "maxPoints": 10000,
                "correlation": {
                    "method": "PEARSON_PAIRWISE_V1",
                    **empty_rule,
                },
                "scatterY": "",
                "scatterDataset": "",
                "trendParameter": "",
                "correlationScope": "",
                "displayGroups": [],
                "pointVisibility": ["IN_SPEC", "OUT_OF_SPEC"],
            },
            "spatial": {
                "mode": "BIN_MAP",
                "parameter": "",
                "maxPoints": 20000,
                "rule": empty_rule,
                "colorScale": "ROBUST",
                "symbolSize": 12,
                "showMissing": True,
            },
            "quality": {
                "analysis": None,
                "parameter": "",
                "groupBy": None,
                "rule": empty_rule,
                "spcOrder": None,
                "spcPhase": None,
                "binType": None,
                "spcDisplayGroup": "",
                "distributionDisplayGroup": "",
                "marginDisplayGroup": "",
                "cooccurrenceDisplayGroup": "",
                "sblDisplayBin": "",
                "sylDisplayDataset": "",
                "percentAxisMode": "AUTO",
            },
            "waferSummary": {"sortBy": "DATASET", "sortDirection": "ASC"},
        },
    }


def _empty_analysis_view_state() -> dict[str, Any]:
    state = _pat_analysis_view_state()
    overview = state["components"]["overviewRisk"]  # type: ignore[index]
    overview["analyses"] = []  # type: ignore[index]
    overview["parameter"] = ""  # type: ignore[index]
    overview["pat"] = {"ruleCode": "", "versionCode": ""}  # type: ignore[index]
    return state


def _request_with_pat_rule(
    *,
    rule_context: SavedAnalysisRuleContext | None = None,
    rule_code: str = "OVERVIEW_PAT",
    version_code: str = "V1",
) -> CreateSavedAnalysisRequest:
    request = _create_request(rule_context=rule_context)
    request.chart_config = {
        "analysis_view_state": _pat_analysis_view_state(
            rule_code=rule_code, version_code=version_code
        )
    }
    return request


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar=None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> _Result:
        return self

    def one_or_none(self):
        if not self._rows:
            return None
        assert len(self._rows) == 1
        return self._rows[0]

    def all(self):
        return list(self._rows)

    def scalar_one(self):
        assert self._scalar is not None
        return self._scalar


@dataclass
class _State:
    datasets: dict[tuple[int, int], dict[str, Any]] = field(default_factory=dict)
    roots: dict[int, dict[str, Any]] = field(default_factory=dict)
    revisions: dict[tuple[int, int], dict[str, Any]] = field(default_factory=dict)
    revision_datasets: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    audits: list[dict[str, Any]] = field(default_factory=list)
    statements: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    next_saved_id: int = 1
    next_revision_id: int = 1
    next_row_version: int = 1
    rule_context: SavedAnalysisRuleContext = field(
        default_factory=SavedAnalysisRuleContext
    )
    fail_dataset_insert_ordinal: int | None = None

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "roots": self.roots,
                "revisions": self.revisions,
                "revision_datasets": self.revision_datasets,
                "audits": self.audits,
                "next_saved_id": self.next_saved_id,
                "next_revision_id": self.next_revision_id,
                "next_row_version": self.next_row_version,
            }
        )

    def restore(self, snapshot: dict[str, Any]) -> None:
        for key, value in snapshot.items():
            setattr(self, key, value)

    def row_version(self) -> bytes:
        value = self.next_row_version.to_bytes(8, "big")
        self.next_row_version += 1
        return value


def _dataset(
    dataset_id: int,
    *,
    owner_user_id: int = 10,
    business_domain: str = "ENGINEERING",
    access_scope: str = "PERSONAL",
    data_domain_id: int | None = None,
    domain_granted: bool = False,
    grant_expired: bool = False,
    domain_active: bool = True,
    status: str = "PUBLISHED",
    is_current: bool = True,
    test_stage: str = "FT",
    spec_set_id: int | None = None,
) -> dict[str, Any]:
    return {
        "dataset_version_id": 1_000 + dataset_id,
        "dataset_id": dataset_id,
        "version_no": 1,
        "status": status,
        "is_current": is_current,
        "spec_set_id": spec_set_id,
        "test_stage": test_stage,
        "owner_user_id": owner_user_id,
        "access_scope": access_scope,
        "data_domain_id": data_domain_id,
        "domain_granted": domain_granted,
        "grant_expired": grant_expired,
        "domain_active": domain_active,
        "supplier_id": 7,
        "product_id": 8,
        "business_domain": business_domain,
        "spec_version": "V1" if spec_set_id is not None else None,
    }


class _Connection:
    def __init__(self, state: _State) -> None:
        self.state = state

    @staticmethod
    def _sql(statement) -> str:
        return " ".join(str(statement).split())

    def execute(self, statement, parameters=None) -> _Result:
        sql = self._sql(statement)
        params = dict(parameters or {})
        self.state.statements.append((sql, params))

        if sql.startswith("SELECT dv.dataset_version_id"):
            row = self.state.datasets.get(
                (int(params["dataset_id"]), int(params["version_no"]))
            )
            if row is None:
                return _Result()
            result = dict(row)
            result["can_read"] = self._can_read_dataset(result, params)
            return _Result([result])

        if sql.startswith("INSERT analysis.saved_analysis("):
            saved_id = self.state.next_saved_id
            self.state.next_saved_id += 1
            now = "2026-08-31T01:00:00"
            root = {
                "saved_analysis_id": saved_id,
                "owner_user_id": int(params["owner_user_id"]),
                "analysis_name": params["analysis_name"],
                "dataset_version_id": int(params["dataset_version_id"]),
                "filter_json": params["filter_json"],
                "chart_config_json": params["chart_config_json"],
                "evaluation_context_json": params["evaluation_context_json"],
                "contract_version": params["contract_version"],
                "filter_hash": params["filter_hash"],
                "context_hash": params["context_hash"],
                "current_revision_no": 1,
                "lifecycle_status": "ACTIVE",
                "row_version": self.state.row_version(),
                "created_at_utc": now,
                "updated_at_utc": now,
            }
            self.state.roots[saved_id] = root
            return _Result([dict(root)])

        if sql.startswith("INSERT analysis.saved_analysis_revision("):
            revision_id = self.state.next_revision_id
            self.state.next_revision_id += 1
            row = {
                "saved_analysis_revision_id": revision_id,
                "saved_analysis_id": int(params["saved_analysis_id"]),
                "revision_no": int(params["revision_no"]),
                "contract_version": params["contract_version"],
                "filter_json": params["filter_json"],
                "filter_hash": params["filter_hash"],
                "context_hash": params["context_hash"],
                "rule_context_json": params["rule_context_json"],
                "chart_config_json": params["chart_config_json"],
                "created_by_user_id": int(params["created_by_user_id"]),
                "created_at_utc": "2026-08-31T01:00:01",
            }
            key = (row["saved_analysis_id"], row["revision_no"])
            if key in self.state.revisions:
                raise AssertionError("duplicate revision escaped the row lock")
            self.state.revisions[key] = row
            self.state.revision_datasets[revision_id] = []
            return _Result([dict(row)])

        if sql.startswith("INSERT analysis.saved_analysis_revision_dataset("):
            ordinal = int(params["ordinal_no"])
            if self.state.fail_dataset_insert_ordinal == ordinal:
                raise RuntimeError("synthetic revision-dataset write failure")
            revision_id = int(params["saved_analysis_revision_id"])
            self.state.revision_datasets[revision_id].append(
                (ordinal, int(params["dataset_version_id"]))
            )
            return _Result()

        if sql.startswith("INSERT governance.audit_log"):
            self.state.audits.append(params)
            return _Result()

        if sql.startswith("SELECT sa.saved_analysis_id") and "OFFSET" in sql:
            roots = self._visible_roots(sql, params)
            offset = int(params["offset"])
            size = int(params["page_size"])
            return _Result([dict(row) for row in roots[offset : offset + size]])

        if sql.startswith("SELECT sa.saved_analysis_id"):
            root = self.state.roots.get(int(params["saved_analysis_id"]))
            return _Result([dict(root)] if root is not None else [])

        if sql.startswith("UPDATE analysis.saved_analysis SET analysis_name"):
            root = self.state.roots[int(params["saved_analysis_id"])]
            if root["row_version"] != params["expected_row_version"]:
                return _Result()
            root.update(
                {
                    "analysis_name": params["analysis_name"],
                    "dataset_version_id": int(params["dataset_version_id"]),
                    "filter_json": params["filter_json"],
                    "chart_config_json": params["chart_config_json"],
                    "evaluation_context_json": params["evaluation_context_json"],
                    "contract_version": params["contract_version"],
                    "filter_hash": params["filter_hash"],
                    "context_hash": params["context_hash"],
                    "current_revision_no": int(params["revision_no"]),
                    "row_version": self.state.row_version(),
                    "updated_at_utc": "2026-08-31T01:00:02",
                }
            )
            return _Result([dict(root)])

        if sql.startswith("SELECT sar.saved_analysis_revision_id"):
            row = self.state.revisions.get(
                (int(params["saved_analysis_id"]), int(params["revision_no"]))
            )
            return _Result([dict(row)] if row is not None else [])

        if sql.startswith("SELECT sard.dataset_version_id"):
            revision_id = int(params["revision_id"])
            rows = []
            by_version_id = {
                int(item["dataset_version_id"]): item
                for item in self.state.datasets.values()
            }
            for ordinal, version_id in sorted(
                self.state.revision_datasets[revision_id]
            ):
                row = dict(by_version_id[version_id])
                row["ordinal_no"] = ordinal
                row["can_read"] = self._can_read_dataset(row, params)
                rows.append(row)
            return _Result(rows)

        if sql.startswith("SELECT COUNT_BIG(*) FROM analysis.saved_analysis"):
            return _Result(scalar=len(self._visible_roots(sql, params)))

        if sql.startswith(
            "UPDATE analysis.saved_analysis SET lifecycle_status='DELETED'"
        ):
            root = self.state.roots[int(params["saved_analysis_id"])]
            if (
                root["row_version"] != params["expected_row_version"]
                or root["lifecycle_status"] != "ACTIVE"
            ):
                return _Result()
            root.update(
                {
                    "lifecycle_status": "DELETED",
                    "row_version": self.state.row_version(),
                    "updated_at_utc": "2026-08-31T01:00:03",
                }
            )
            return _Result([dict(root)])

        raise AssertionError(f"unexpected SQL: {sql}")

    def _visible_roots(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        roots = list(self.state.roots.values())
        if "sa.lifecycle_status='ACTIVE'" in sql:
            roots = [row for row in roots if row["lifecycle_status"] == "ACTIVE"]
        visible = []
        for root in roots:
            revision = self.state.revisions[
                (int(root["saved_analysis_id"]), int(root["current_revision_no"]))
            ]
            version_ids = self.state.revision_datasets[
                int(revision["saved_analysis_revision_id"])
            ]
            datasets = [
                next(
                    item
                    for item in self.state.datasets.values()
                    if int(item["dataset_version_id"]) == version_id
                )
                for _, version_id in version_ids
            ]
            if all(self._can_read_dataset(item, params) for item in datasets):
                visible.append(root)
        roots = visible
        return sorted(
            roots,
            key=lambda row: (row["updated_at_utc"], row["saved_analysis_id"]),
            reverse=True,
        )

    @staticmethod
    def _can_read_dataset(
        row: dict[str, Any], params: dict[str, Any]
    ) -> bool:
        return bool(
            params.get("has_data_break_glass")
            or (
                row["access_scope"] == "PERSONAL"
                and int(row["owner_user_id"]) == int(params["user_id"])
            )
            or (
                row["access_scope"] == "DOMAIN"
                and row["data_domain_id"] is not None
                and bool(row["domain_granted"])
                and not bool(row.get("grant_expired"))
                and bool(row.get("domain_active", True))
                and row["status"] == "PUBLISHED"
                and bool(row["is_current"])
            )
        )


class _ConnectContext(AbstractContextManager[_Connection]):
    def __init__(self, engine: _Engine, transactional: bool) -> None:
        self.engine = engine
        self.transactional = transactional
        self.snapshot: dict[str, Any] | None = None

    def __enter__(self) -> _Connection:
        self.engine.lock.acquire()
        if self.transactional:
            self.snapshot = self.engine.state.snapshot()
        return _Connection(self.engine.state)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is not None and self.snapshot is not None:
            self.engine.state.restore(self.snapshot)
        self.engine.lock.release()


class _Engine:
    def __init__(self, state: _State) -> None:
        self.state = state
        self.lock = threading.RLock()

    def begin(self) -> _ConnectContext:
        return _ConnectContext(self, True)

    def connect(self) -> _ConnectContext:
        return _ConnectContext(self, False)


def _service(
    state: _State, approved_rule_resolver: Any | None = None
) -> SqlSavedAnalysisService:
    def resolve_rule_context(connection, _dataset_rows, _context):
        return connection.state.rule_context

    return SqlSavedAnalysisService(  # type: ignore[arg-type]
        _Engine(state),
        rule_context_resolver=resolve_rule_context,
        approved_rule_resolver=approved_rule_resolver,
    )


def test_create_writes_root_revision_eight_exact_datasets_and_safe_audit() -> None:
    state = _State(
        datasets={(dataset_id, 1): _dataset(dataset_id) for dataset_id in range(1, 9)}
    )
    created = _service(state).create(
        _create_request(tuple(range(1, 9))), _principal(10)
    )

    assert created.current_revision_no == 1
    assert created.row_version == "0000000000000001"
    assert [item.dataset_id for item in created.revision.datasets] == list(range(1, 9))
    assert [item.ordinal_no for item in created.revision.datasets] == list(range(1, 9))
    assert len(state.roots) == len(state.revisions) == 1
    assert len(state.revision_datasets[1]) == 8
    statements = "\n".join(sql for sql, _ in state.statements)
    assert "INSERT analysis.saved_analysis(" in statements
    assert "INSERT analysis.saved_analysis_revision(" in statements
    assert "INSERT analysis.saved_analysis_revision_dataset(" in statements
    assert "INSERT governance.audit_log" in statements
    audit = state.audits[0]
    assert "SYNTHETIC-A" not in str(audit)
    assert audit["operation"] == "SAVED_ANALYSIS_CREATE"


def test_hashes_are_server_deterministic_for_order_only_changes() -> None:
    first = _create_request((1, 2))
    reordered = first.model_copy(deep=True)
    reordered.datasets.reverse()
    reordered.parameters.reverse()
    reordered.filters.lot_ids.reverse()
    assert saved_analysis_hashes(first) == saved_analysis_hashes(reordered)


def test_saved_hashes_match_live_analytics_contract_for_non_ascii_parameter() -> None:
    request = _create_request()
    request.parameters = ["阈值电压"]
    saved = saved_analysis_hashes(request)
    live = analytics_hashes(
        AnalyticsContextRequest(
            datasets=request.datasets,
            filters=request.filters,
            parameters=request.parameters,
        )
    )
    assert saved.filter_hash == live.filter_hash
    assert saved.context_hash == live.context_hash


def test_saved_state_rejects_extra_or_oversized_json_fields() -> None:
    payload = _create_request().model_dump(mode="json")
    payload["client_filter_hash"] = "0" * 64
    with pytest.raises(ValidationError):
        CreateSavedAnalysisRequest.model_validate(payload)

    payload.pop("client_filter_hash")
    payload["chart_config"] = {"title": "x" * 4_001}
    with pytest.raises(ValidationError, match="oversized string"):
        CreateSavedAnalysisRequest.model_validate(payload)


def test_legacy_saved_analysis_state_remains_read_compatible() -> None:
    payload = _create_request().model_dump(mode="json")
    payload.pop("analysis_name")
    payload.pop("change_reason")
    payload["contract_version"] = "ANALYTICS_CONTEXT_V1"
    payload["chart_config"] = {}

    legacy = SavedAnalysisState.model_validate(payload)

    assert legacy.contract_version == "ANALYTICS_CONTEXT_V1"
    assert legacy.chart_config == {}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda payload: payload.update(
                {"contract_version": "ANALYTICS_CONTEXT_V1"}
            ),
            "contract_version must be SAVED_ANALYSIS_V1",
        ),
        (
            lambda payload: payload.update({"chart_config": {}}),
            "analysis_view_state must be an object",
        ),
        (
            lambda payload: payload["chart_config"].update({"analysis_view_state": []}),
            "validation error",
        ),
        (
            lambda payload: payload["chart_config"]["analysis_view_state"].update(
                {"contract_version": "ANALYSIS_VIEW_STATE_V0"}
            ),
            "ANALYSIS_VIEW_STATE_V1",
        ),
    ],
)
def test_saved_write_requests_reject_incomplete_or_stale_state_contracts(
    mutation: Any, message: str
) -> None:
    create_payload = _create_request().model_dump(mode="json")
    mutation(create_payload)
    with pytest.raises(ValidationError, match=message):
        CreateSavedAnalysisRequest.model_validate(create_payload)

    revision_payload = _revision_request("0000000000000001").model_dump(mode="json")
    mutation(revision_payload)
    with pytest.raises(ValidationError, match=message):
        CreateSavedAnalysisRevisionRequest.model_validate(revision_payload)


def test_create_rejects_stale_rule_context_and_rolls_back() -> None:
    state = _State(datasets={(1, 1): _dataset(1)})
    request = _create_request(
        rule_context=SavedAnalysisRuleContext(spec_versions=["SPEC:9:STALE"])
    )
    with pytest.raises(DomainError) as captured:
        _service(state).create(request, _principal(10))
    assert captured.value.code == "SAVED_ANALYSIS_RULE_CONTEXT_STALE"
    assert state.roots == {}
    assert state.revisions == {}


def test_create_validates_and_freezes_server_derived_view_rule() -> None:
    state = _State(datasets={(1, 1): _dataset(1)})
    calls: list[dict[str, Any]] = []

    def approved_rule(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"iqr_multiplier": 1.5}

    created = _service(state, approved_rule).create(
        _request_with_pat_rule(), _principal(10)
    )

    assert created.revision.rule_context.evaluation_rule_versions == [
        "RULE:OVERVIEW_PAT:V1"
    ]
    assert calls == [
        {
            "rule_code": "OVERVIEW_PAT",
            "version_code": "V1",
            "test_stage": "FT",
            "expected_algorithm_code": "PAT_SHARED_IQR_1_35_V1",
            "supplier_id": 7,
            "product_id": 8,
            "parameter": "PARAM_A",
        }
    ]
    stored = state.revisions[(created.saved_analysis_id, 1)]["rule_context_json"]
    assert json.loads(stored)["evaluation_rule_versions"] == ["RULE:OVERVIEW_PAT:V1"]


def test_create_rejects_client_injected_rule_not_selected_by_view() -> None:
    state = _State(datasets={(1, 1): _dataset(1)})

    with pytest.raises(DomainError) as captured:
        _service(state, lambda **_kwargs: {}).create(
            _request_with_pat_rule(
                rule_context=SavedAnalysisRuleContext(
                    evaluation_rule_versions=["RULE:CLIENT_INJECTED:V9"]
                )
            ),
            _principal(10),
        )

    assert captured.value.code == "SAVED_ANALYSIS_RULE_CONTEXT_STALE"
    assert state.roots == {}


def test_create_fails_closed_when_selected_view_rule_is_not_approved() -> None:
    state = _State(datasets={(1, 1): _dataset(1)})

    def revoked(**_kwargs: Any) -> dict[str, Any]:
        raise DomainError("ANALYSIS_RULE_NOT_APPROVED", "approval was revoked", 409)

    with pytest.raises(DomainError) as captured:
        _service(state, revoked).create(_request_with_pat_rule(), _principal(10))

    assert captured.value.code == "ANALYSIS_RULE_NOT_APPROVED"
    assert state.roots == {}
    assert state.revisions == {}


def test_restore_marks_saved_view_rule_changed_after_approval_is_revoked() -> None:
    state = _State(datasets={(1, 1): _dataset(1)})
    gate = {"approved": True}

    def approved_rule(**_kwargs: Any) -> dict[str, Any]:
        if not gate["approved"]:
            raise DomainError("ANALYSIS_RULE_NOT_APPROVED", "approval was revoked", 409)
        return {"iqr_multiplier": 1.5}

    service = _service(state, approved_rule)
    created = service.create(_request_with_pat_rule(), _principal(10))
    gate["approved"] = False

    restored = service.get(created.saved_analysis_id, _principal(10))

    assert restored.restore_status == "RULE_CHANGED"
    assert restored.revision.rule_context.evaluation_rule_versions == [
        "RULE:OVERVIEW_PAT:V1"
    ]


def test_revision_revalidates_changed_exact_rule_and_freezes_new_identity() -> None:
    state = _State(datasets={(1, 1): _dataset(1)})
    calls: list[tuple[str, str]] = []

    def approved_rule(**kwargs: Any) -> dict[str, Any]:
        calls.append((kwargs["rule_code"], kwargs["version_code"]))
        return {"iqr_multiplier": 1.5}

    service = _service(state, approved_rule)
    created = service.create(_create_request(), _principal(10))
    revision = _revision_request(created.row_version)
    revision.chart_config = {
        "analysis_view_state": _pat_analysis_view_state(
            rule_code="OVERVIEW_PAT", version_code="V2"
        )
    }

    revised = service.create_revision(
        created.saved_analysis_id, revision, _principal(10)
    )

    assert calls == [("OVERVIEW_PAT", "V2")]
    assert revised.revision.rule_context.evaluation_rule_versions == [
        "RULE:OVERVIEW_PAT:V2"
    ]


def test_selected_view_rule_with_missing_version_fails_closed() -> None:
    state = _State(datasets={(1, 1): _dataset(1)})
    request = _request_with_pat_rule()
    view = request.chart_config["analysis_view_state"]
    view["components"]["overviewRisk"]["pat"]["versionCode"] = ""  # type: ignore[index]

    with pytest.raises(DomainError) as captured:
        _service(state, lambda **_kwargs: {}).create(request, _principal(10))

    assert captured.value.code == "SAVED_ANALYSIS_VIEW_CONFIG_INVALID"
    assert state.roots == {}


def test_read_fails_closed_after_domain_access_is_revoked_without_decoding_state() -> (
    None
):
    state = _State(
        datasets={
            (1, 1): _dataset(
                1,
                owner_user_id=99,
                business_domain="PRODUCTION",
                access_scope="DOMAIN",
                data_domain_id=7,
                domain_granted=True,
            )
        }
    )
    service = _service(state)
    owner = _principal(10)
    created = service.create(_create_request(), owner)

    state.datasets[(1, 1)]["is_current"] = False
    statement_count = len(state.statements)
    with pytest.raises(DomainError) as revoked:
        service.get(created.saved_analysis_id, owner)
    assert revoked.value.code == "SAVED_ANALYSIS_NOT_FOUND"
    read_sql = [sql for sql, _ in state.statements[statement_count:]]
    assert not any("sar.filter_json" in sql for sql in read_sql)
    assert service.list_page(owner, page=1, page_size=20).items == ()

    state.datasets[(1, 1)].update(
        {
            "owner_user_id": 10,
            "access_scope": "PERSONAL",
            "data_domain_id": None,
            "domain_granted": False,
        }
    )
    non_current = service.get(created.saved_analysis_id, owner)
    assert non_current.restore_status == "NON_CURRENT"
    assert non_current.revision.datasets[0].version_no == 1

    state.datasets[(1, 1)]["is_current"] = True
    state.rule_context = SavedAnalysisRuleContext(
        evaluation_rule_versions=["RULE:77:V2"]
    )
    rule_changed = service.get(created.saved_analysis_id, owner)
    assert rule_changed.restore_status == "RULE_CHANGED"
    assert rule_changed.revision.rule_context.evaluation_rule_versions == []


@pytest.mark.parametrize(
    "dataset_change",
    (
        {"domain_granted": False},
        {"grant_expired": True},
        {"domain_active": False},
    ),
    ids=("revoked", "expired", "domain-disabled"),
)
def test_domain_access_loss_hides_saved_state_from_owner_and_system_admin(
    dataset_change: dict[str, Any],
) -> None:
    state = _State(
        datasets={
            (1, 1): _dataset(
                1,
                owner_user_id=99,
                access_scope="DOMAIN",
                data_domain_id=7,
                domain_granted=True,
            )
        }
    )
    service = _service(state)
    owner = _principal(10)
    created = service.create(_create_request(), owner)
    state.datasets[(1, 1)].update(dataset_change)

    for principal in (owner, _principal(1, admin=True)):
        with pytest.raises(DomainError) as hidden:
            service.get(created.saved_analysis_id, principal)
        assert hidden.value.code == "SAVED_ANALYSIS_NOT_FOUND"
        page = service.list_page(principal, page=1, page_size=20)
        assert page.total == 0
        assert page.items == ()


def test_personal_cross_owner_is_hidden_but_granted_domain_is_read_only() -> (
    None
):
    state = _State(datasets={(1, 1): _dataset(1, owner_user_id=10)})
    service = _service(state)
    created = service.create(_create_request(), _principal(10))

    with pytest.raises(DomainError) as hidden:
        service.get(created.saved_analysis_id, _principal(20))
    assert hidden.value.code == "SAVED_ANALYSIS_NOT_FOUND"

    state.datasets[(1, 1)].update(
        {
            "business_domain": "PRODUCTION",
            "access_scope": "DOMAIN",
            "data_domain_id": 7,
            "domain_granted": True,
        }
    )
    shared = service.get(created.saved_analysis_id, _principal(20))
    assert shared.restore_status == "CURRENT"
    visibility_sql = "\n".join(statement for statement, _ in state.statements)
    assert "iam.data_domain_grant" in visibility_sql
    assert "business_domain='PRODUCTION'" not in visibility_sql
    with pytest.raises(DomainError) as revise:
        service.create_revision(
            created.saved_analysis_id,
            _revision_request(created.row_version),
            _principal(20),
        )
    assert revise.value.code == "SAVED_ANALYSIS_OWNER_REQUIRED"
    with pytest.raises(DomainError) as admin_revise:
        service.create_revision(
            created.saved_analysis_id,
            _revision_request(created.row_version),
            _principal(1, admin=True),
        )
    assert admin_revise.value.code == "SAVED_ANALYSIS_OWNER_REQUIRED"
    with pytest.raises(DomainError) as delete:
        service.delete(
            created.saved_analysis_id,
            DeleteSavedAnalysisRequest(
                expected_row_version=created.row_version,
                reason="Cross-owner delete must remain forbidden",
            ),
            _principal(20),
        )
    assert delete.value.code == "SAVED_ANALYSIS_OWNER_REQUIRED"


def test_private_analysis_hides_existence_before_decoding_saved_json() -> None:
    state = _State(datasets={(1, 1): _dataset(1, owner_user_id=10)})
    service = _service(state)
    created = service.create(_create_request(), _principal(10))
    state.revisions[(created.saved_analysis_id, 1)]["filter_json"] = "not-json"

    with pytest.raises(DomainError) as hidden:
        service.get(created.saved_analysis_id, _principal(20))

    assert hidden.value.code == "SAVED_ANALYSIS_NOT_FOUND"


def test_concurrent_revisions_are_serialized_by_lock_and_row_version() -> None:
    state = _State(datasets={(1, 1): _dataset(1)})
    service = _service(state)
    owner = _principal(10)
    created = service.create(_create_request(), owner)
    request = _revision_request(created.row_version)
    barrier = threading.Barrier(2)
    results: list[object] = []

    def revise() -> None:
        barrier.wait()
        try:
            results.append(
                service.create_revision(created.saved_analysis_id, request, owner)
            )
        except DomainError as exc:
            results.append(exc)

    threads = [threading.Thread(target=revise) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(results) == 2
    assert sum(not isinstance(item, DomainError) for item in results) == 1
    errors = [item for item in results if isinstance(item, DomainError)]
    assert errors[0].code == "SAVED_ANALYSIS_ROW_VERSION_CONFLICT"
    assert state.roots[1]["current_revision_no"] == 2
    assert sorted(state.revisions) == [(1, 1), (1, 2)]
    root_lock_sql = next(
        sql
        for sql, _ in state.statements
        if "FROM analysis.saved_analysis sa WITH (UPDLOCK,HOLDLOCK)" in sql
    )
    assert "UPDLOCK,HOLDLOCK" in root_lock_sql
    update_sql = next(
        sql
        for sql, _ in state.statements
        if sql.startswith("UPDATE analysis.saved_analysis SET analysis_name")
    )
    assert "AND row_version=:expected_row_version" in update_sql


def test_failed_revision_rolls_back_root_revision_links_and_audit() -> None:
    state = _State(
        datasets={(dataset_id, 1): _dataset(dataset_id) for dataset_id in (1, 2)}
    )
    service = _service(state)
    owner = _principal(10)
    created = service.create(_create_request(), owner)
    state.fail_dataset_insert_ordinal = 2

    with pytest.raises(RuntimeError, match="synthetic revision-dataset"):
        service.create_revision(
            created.saved_analysis_id,
            _revision_request(created.row_version, (1, 2)),
            owner,
        )

    assert state.roots[1]["current_revision_no"] == 1
    assert sorted(state.revisions) == [(1, 1)]
    assert state.revision_datasets == {1: [(1, 1001)]}
    assert [item["operation"] for item in state.audits] == ["SAVED_ANALYSIS_CREATE"]


def test_list_and_logical_delete_preserve_revision_history() -> None:
    state = _State(datasets={(1, 1): _dataset(1)})
    service = _service(state)
    owner = _principal(10)
    created = service.create(_create_request(), owner)

    page = service.list_page(owner, page=1, page_size=20)
    assert page.total == 1
    assert page.items[0].saved_analysis_id == created.saved_analysis_id

    deleted = service.delete(
        created.saved_analysis_id,
        DeleteSavedAnalysisRequest(
            expected_row_version=created.row_version,
            reason="Retire obsolete saved analysis configuration",
        ),
        owner,
    )
    assert deleted.lifecycle_status == "DELETED"
    assert state.revisions[(1, 1)]["revision_no"] == 1
    assert service.list_page(owner, page=1, page_size=20).total == 0
    with pytest.raises(DomainError) as include_deleted:
        service.list_page(owner, page=1, page_size=20, include_deleted=True)
    assert include_deleted.value.code == "SAVED_ANALYSIS_ADMIN_REQUIRED"
    admin_page = service.list_page(
        _principal(1, admin=True), page=1, page_size=20, include_deleted=True
    )
    assert admin_page.items == ()
    owner_admin_page = service.list_page(
        _principal(10, admin=True), page=1, page_size=20, include_deleted=True
    )
    assert owner_admin_page.items[0].lifecycle_status == "DELETED"
