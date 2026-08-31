from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, bindparam, text

from app.core.errors import DomainError
from app.domain.analytics import AnalyticsContextRequest


@dataclass(frozen=True, slots=True)
class FormalSpecContextResolution:
    """Version identities used by exact filtered historic Run/parameter scopes."""

    spec_versions: tuple[str, ...]
    resolved_scope_count: int
    no_spec_scope_count: int


def _source_identity(row: Mapping[str, Any]) -> str:
    metadata: Mapping[str, Any] = {}
    try:
        decoded = json.loads(row.get("metadata_json") or "{}")
        if isinstance(decoded, dict):
            metadata = decoded
    except (TypeError, ValueError):
        metadata = {}
    explicit = str(metadata.get("source_id") or "").strip()
    return explicit or f"RUN-{int(row['run_id'])}"


def _condition_text(value: object) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise DomainError(
            "ANALYSIS_SPEC_CONTRACT_INVALID",
            "test-condition metadata is not valid JSON",
            409,
        ) from exc
    if not isinstance(decoded, dict) or not set(decoded).issubset(
        {"text", "bias1", "bias2"}
    ):
        raise DomainError(
            "ANALYSIS_SPEC_CONTRACT_INVALID",
            "test-condition metadata contains unsupported fields",
            409,
        )
    normalized: dict[str, str] = {}
    for key in ("text", "bias1", "bias2"):
        raw = decoded.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise DomainError(
                "ANALYSIS_SPEC_CONTRACT_INVALID",
                "test-condition metadata contains a non-text value",
                409,
            )
        compact = " ".join(raw.split())
        if compact:
            normalized[key] = compact
    if not normalized:
        return None
    if set(normalized) == {"text"}:
        return normalized["text"]
    return json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _expanding_statement(sql: str, expanding: Sequence[str]):
    statement = text(sql)
    for name in expanding:
        statement = statement.bindparams(bindparam(name, expanding=True))
    return statement


def formal_spec_context_from_rows(
    rows: Sequence[Mapping[str, Any]],
) -> FormalSpecContextResolution:
    """Reduce query rows while preserving every exact historic Spec version."""

    scopes: dict[tuple[object, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("event_at_utc") is None:
            raise DomainError(
                "ANALYSIS_SPEC_EVENT_TIME_REQUIRED",
                "Formal Spec context requires a stable Run event_at_utc",
                409,
            )
        scopes[
            (
                row.get("dataset_version_id"),
                row.get("run_id"),
                row.get("lot_id"),
                row.get("test_item_id"),
            )
        ].append(row)

    versions: set[str] = set()
    resolved = 0
    no_spec = 0
    for scope, values in scopes.items():
        stages = {str(row.get("test_stage") or "") for row in values}
        if len(stages) != 1 or next(iter(stages)) not in {"CP", "FT"}:
            raise DomainError(
                "ANALYSIS_SPEC_CONTEXT_AMBIGUOUS",
                "Formal Spec scope has an invalid test stage",
                409,
                details=[{"scope": list(scope)}],
            )

        candidates = list(values)
        if next(iter(stages)) == "FT":
            eligible = [
                row
                for row in values
                if row.get("spec_binding_id") is not None
                and row.get("scope_priority") is not None
            ]
            if not eligible:
                no_spec += 1
                continue
            highest = max(int(row["scope_priority"]) for row in eligible)
            top = [row for row in eligible if int(row["scope_priority"]) == highest]
            binding_ids = {int(row["spec_binding_id"]) for row in top}
            if len(binding_ids) != 1:
                raise DomainError(
                    "ANALYSIS_SPEC_CONTEXT_AMBIGUOUS",
                    "more than one highest-priority Formal Spec binding matches a historic Run scope",
                    409,
                    details=[
                        {
                            "scope": list(scope),
                            "binding_ids": sorted(binding_ids),
                            "priority": highest,
                        }
                    ],
                )
            candidates = top

        covered = [
            row
            for row in candidates
            if row.get("spec_set_id") is not None
            and row.get("version_code") is not None
            and str(row.get("version_code")).strip()
            and row.get("spec_item_id") is not None
        ]
        if not covered:
            no_spec += 1
            continue
        identities = {
            (
                int(row["spec_set_id"]),
                str(row["version_code"]).strip(),
                int(row["spec_item_id"]),
            )
            for row in covered
        }
        if len(identities) != 1:
            raise DomainError(
                "ANALYSIS_SPEC_CONTEXT_AMBIGUOUS",
                "more than one Formal Spec item matches a historic Run parameter scope",
                409,
                details=[{"scope": list(scope)}],
            )
        spec_set_id, version_code, _ = next(iter(identities))
        versions.add(f"SPEC:{spec_set_id}:{version_code}")
        resolved += 1

    return FormalSpecContextResolution(
        spec_versions=tuple(sorted(versions)),
        resolved_scope_count=resolved,
        no_spec_scope_count=no_spec,
    )


def resolve_formal_spec_context(
    connection: Connection,
    dataset_rows: Sequence[Mapping[str, Any]],
    request: AnalyticsContextRequest,
) -> FormalSpecContextResolution:
    """Resolve exact Formal Specs at Run event time, never at the current clock."""

    selected_parameters = tuple(request.parameters)
    if not selected_parameters:
        return FormalSpecContextResolution((), 0, 0)

    all_rows: list[Mapping[str, Any]] = []
    for dataset in dataset_rows:
        dataset_version_id = int(dataset["dataset_version_id"])
        source_run_ids: tuple[int, ...] | None = None
        if request.filters.source_ids:
            source_rows = tuple(
                connection.execute(
                    text(
                        "/* ANALYTICS_FORMAL_SPEC_SOURCE_RUNS_V1 */ "
                        "SELECT DISTINCT tr.run_id,tr.metadata_json "
                        "FROM dataset.dataset_version_run dvr "
                        "JOIN test.test_run tr "
                        "ON tr.processing_run_id=dvr.processing_run_id "
                        "WHERE dvr.dataset_version_id=:dataset_version_id"
                    ),
                    {"dataset_version_id": dataset_version_id},
                )
                .mappings()
                .all()
            )
            wanted = set(request.filters.source_ids)
            source_run_ids = tuple(
                sorted(
                    int(row["run_id"])
                    for row in source_rows
                    if _source_identity(row) in wanted
                )
            )

        condition_item_ids: tuple[int, ...] | None = None
        if request.filters.test_conditions:
            condition_rows = tuple(
                connection.execute(
                    text(
                        "/* ANALYTICS_FORMAL_SPEC_CONDITIONS_V1 */ "
                        "SELECT DISTINCT tid.test_item_id,tid.condition_json "
                        "FROM dataset.dataset_version_run dvr "
                        "JOIN test.test_run tr "
                        "ON tr.processing_run_id=dvr.processing_run_id "
                        "JOIN mdm.test_item_definition tid "
                        "ON tid.program_version_id=tr.program_version_id "
                        "WHERE dvr.dataset_version_id=:dataset_version_id"
                    ),
                    {"dataset_version_id": dataset_version_id},
                )
                .mappings()
                .all()
            )
            wanted_conditions = set(request.filters.test_conditions)
            condition_item_ids = tuple(
                sorted(
                    int(row["test_item_id"])
                    for row in condition_rows
                    if _condition_text(row.get("condition_json")) in wanted_conditions
                )
            )

        clauses = [
            "dvr.dataset_version_id=:dataset_version_id",
            "tid.raw_item_name IN :formal_parameters",
        ]
        parameters: dict[str, object] = {
            "dataset_version_id": dataset_version_id,
            "formal_parameters": selected_parameters,
            "dataset_spec_set_id": dataset.get("spec_set_id"),
        }
        expanding = ["formal_parameters"]
        filters = request.filters
        for name, values, clause in (
            ("formal_lot_ids", tuple(filters.lot_ids), "tr.lot_id IN :formal_lot_ids"),
            (
                "formal_wafer_ids",
                tuple(filters.wafer_ids),
                "COALESCE(ur.wafer_id,tr.wafer_id) IN :formal_wafer_ids",
            ),
            (
                "formal_bin_codes",
                tuple(filters.bin_codes),
                "COALESCE(ur.soft_bin,ur.hard_bin,N'UNKNOWN') IN :formal_bin_codes",
            ),
            (
                "formal_overall_results",
                tuple(item.value for item in filters.overall_results),
                "ur.overall_result IN :formal_overall_results",
            ),
            (
                "formal_tester_ids",
                tuple(filters.tester_ids),
                "tr.tester_id IN :formal_tester_ids",
            ),
            (
                "formal_program_versions",
                tuple(filters.program_versions),
                "pv.version_code IN :formal_program_versions",
            ),
        ):
            if values:
                clauses.append(clause)
                parameters[name] = values
                expanding.append(name)
        if source_run_ids is not None:
            if source_run_ids:
                clauses.append("tr.run_id IN :formal_source_run_ids")
                parameters["formal_source_run_ids"] = source_run_ids
                expanding.append("formal_source_run_ids")
            else:
                clauses.append("1=0")
        if condition_item_ids is not None:
            if condition_item_ids:
                clauses.append(
                    "EXISTS(SELECT 1 FROM test.measurement formal_condition_m "
                    "WHERE formal_condition_m.unit_id=ur.unit_id "
                    "AND formal_condition_m.test_item_id IN :formal_condition_item_ids)"
                )
                parameters["formal_condition_item_ids"] = condition_item_ids
                expanding.append("formal_condition_item_ids")
            else:
                clauses.append("1=0")

        event_time = "COALESCE(tr.started_at_utc,pr.started_at_utc)"
        stage = str(dataset["test_stage"])
        if stage == "CP":
            spec_join = (
                "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=:dataset_spec_set_id "
                "AND ss.status='RELEASED' "
                f"AND (ss.effective_from_utc IS NULL OR ss.effective_from_utc<={event_time}) "
                f"AND (ss.effective_to_utc IS NULL OR ss.effective_to_utc>{event_time}) "
                "LEFT JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
                "AND si.test_item_id=tid.test_item_id "
                "LEFT JOIN mdm.spec_binding sb ON 1=0 "
                "LEFT JOIN mdm.scope_priority sp ON 1=0 "
            )
        elif stage == "FT":
            spec_join = (
                "LEFT JOIN mdm.spec_binding sb ON 1=1 "
                "AND (sb.program_version_id IS NULL OR sb.program_version_id=tr.program_version_id) "
                "AND (sb.product_id IS NULL OR sb.product_id=tr.product_id) "
                "AND (sb.supplier_id IS NULL OR sb.supplier_id=tr.supplier_id) "
                "AND (sb.test_stage IS NULL OR sb.test_stage=tr.test_stage) "
                f"AND (sb.effective_from_utc IS NULL OR sb.effective_from_utc<={event_time}) "
                f"AND (sb.effective_to_utc IS NULL OR sb.effective_to_utc>{event_time}) "
                "LEFT JOIN mdm.scope_priority sp ON sp.scope_code=sb.scope_code "
                "AND sp.active=1 "
                "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=sb.spec_set_id "
                "AND ss.status='RELEASED' "
                f"AND (ss.effective_from_utc IS NULL OR ss.effective_from_utc<={event_time}) "
                f"AND (ss.effective_to_utc IS NULL OR ss.effective_to_utc>{event_time}) "
                "LEFT JOIN mdm.spec_item si ON si.spec_set_id=ss.spec_set_id "
                "AND si.test_item_id=tid.test_item_id "
            )
        else:
            raise DomainError(
                "ANALYSIS_STAGE_INCOMPATIBLE",
                "Formal Spec context accepts only CP or FT Dataset Versions",
                409,
            )

        sql = (
            "/* ANALYTICS_FORMAL_SPEC_CONTEXT_V1 */ "
            "SELECT DISTINCT dvr.dataset_version_id,tr.run_id,tr.lot_id,tr.test_stage,"
            "tr.program_version_id AS run_program_version_id,"
            "tid.program_version_id AS item_program_version_id,tid.test_item_id,"
            "tid.raw_item_name,"
            f"{event_time} AS event_at_utc,"
            "sb.spec_binding_id,sp.priority AS scope_priority,"
            "ss.spec_set_id,ss.version_code,si.spec_item_id "
            "FROM dataset.dataset_version_run dvr "
            "JOIN ingestion.processing_run pr "
            "ON pr.processing_run_id=dvr.processing_run_id "
            "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
            "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
            "LEFT JOIN mdm.test_program_version pv "
            "ON pv.program_version_id=tr.program_version_id "
            "JOIN test.measurement m ON m.unit_id=ur.unit_id "
            "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
            "AND tid.program_version_id=tr.program_version_id "
            + spec_join
            + "WHERE "
            + " AND ".join(clauses)
            + " ORDER BY tr.run_id,tr.lot_id,tid.test_item_id,sp.priority DESC,"
            "sb.spec_binding_id,ss.spec_set_id,si.spec_item_id"
        )
        all_rows.extend(
            connection.execute(_expanding_statement(sql, expanding), parameters)
            .mappings()
            .all()
        )
    resolution = formal_spec_context_from_rows(all_rows)
    if resolution.no_spec_scope_count:
        raise DomainError(
            "ANALYSIS_SPEC_CONTEXT_INCOMPLETE",
            "one or more selected historic Run/parameter scopes have no exact effective Formal Spec",
            409,
            details=[
                {
                    "resolved_scope_count": resolution.resolved_scope_count,
                    "no_spec_scope_count": resolution.no_spec_scope_count,
                }
            ],
        )
    return resolution
