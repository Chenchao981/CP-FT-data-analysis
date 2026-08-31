from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.core.errors import DomainError


def _finite(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DomainError(
            "ANALYSIS_SPEC_CONTRACT_INVALID", f"{field} is not numeric", 409
        ) from exc
    if not math.isfinite(numeric):
        raise DomainError(
            "ANALYSIS_SPEC_CONTRACT_INVALID", f"{field} is not finite", 409
        )
    return numeric


def _operator(
    value: object,
    *,
    limit: object,
    allowed: frozenset[str],
    field: str,
) -> str | None:
    if limit is None:
        return None
    if not isinstance(value, str) or value.strip() not in allowed:
        raise DomainError(
            "ANALYSIS_SPEC_OPERATOR_INVALID",
            f"{field} is required and must use an approved comparison operator",
            409,
        )
    return value.strip()


def _condition(value: object, *, parameter: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise DomainError(
            "ANALYSIS_SPEC_CONTRACT_INVALID",
            f"parameter {parameter} has invalid formal Spec condition metadata",
            409,
        ) from exc
    if not isinstance(decoded, dict) or not set(decoded).issubset(
        {"text", "bias1", "bias2"}
    ):
        raise DomainError(
            "ANALYSIS_SPEC_CONTRACT_INVALID",
            f"parameter {parameter} has unsupported formal Spec condition metadata",
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
                f"parameter {parameter} has invalid formal Spec condition metadata",
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


@dataclass(frozen=True, slots=True)
class FormalSpecResolution:
    status: str
    reason_codes: tuple[str, ...]
    spec_set_ids: tuple[int, ...]
    spec_versions: tuple[str, ...]
    unit: str | None
    test_condition: str | None
    lsl: float | None
    usl: float | None
    lower_operator: str | None = None
    upper_operator: str | None = None

    @property
    def resolved(self) -> bool:
        return self.status == "RESOLVED"


def resolve_released_formal_spec(
    rows: Sequence[Mapping[str, Any]],
    *,
    parameter: str,
    identity_unit: str | None,
    identity_condition: str | None,
) -> FormalSpecResolution:
    """Resolve one compatible Released Spec across every selected run/Lot/item scope."""

    if not rows:
        return FormalSpecResolution(
            status="NO_SPEC",
            reason_codes=("FORMAL_RELEASED_SPEC_NOT_FOUND",),
            spec_set_ids=(),
            spec_versions=(),
            unit=None,
            test_condition=None,
            lsl=None,
            usl=None,
            lower_operator=None,
            upper_operator=None,
        )

    scopes: dict[tuple[object, object, object], list[Mapping[str, Any]]] = defaultdict(
        list
    )
    for row in rows:
        scopes[(row.get("run_id"), row.get("lot_id"), row.get("test_item_id"))].append(
            row
        )

    reasons: list[str] = []
    spec_ids: set[int] = set()
    spec_versions: set[str] = set()
    signatures: set[
        tuple[
            str | None,
            float | None,
            float | None,
            str | None,
            str | None,
            str | None,
        ]
    ] = set()
    for values in scopes.values():
        if any(
            row.get("run_program_version_id") is not None
            and row.get("run_program_version_id") != row.get("item_program_version_id")
            for row in values
        ):
            reasons.append("FORMAL_SPEC_SCOPE_NOT_COVERED")
            continue
        candidates = list(values)
        stages = {str(row.get("test_stage") or "").upper() for row in values}
        if stages == {"FT"}:
            eligible = [
                row
                for row in values
                if row.get("spec_binding_id") is not None
                and row.get("scope_priority") is not None
            ]
            if not eligible:
                reasons.append("FORMAL_SPEC_SCOPE_NOT_COVERED")
                continue
            highest = max(int(row["scope_priority"]) for row in eligible)
            top = [row for row in eligible if int(row["scope_priority"]) == highest]
            if len({int(row["spec_binding_id"]) for row in top}) != 1:
                reasons.append("SPEC_CONTEXT_AMBIGUOUS")
                continue
            candidates = top
        covered = [
            row
            for row in candidates
            if row.get("spec_set_id") is not None
            and row.get("spec_item_id") is not None
            and row.get("version_code") is not None
            and str(row.get("version_code")).strip()
        ]
        if not covered:
            reasons.append("FORMAL_SPEC_SCOPE_NOT_COVERED")
            continue
        scope_ids = {int(row["spec_set_id"]) for row in covered}
        scope_versions = {
            f"SPEC:{int(row['spec_set_id'])}:{str(row['version_code']).strip()}"
            for row in covered
        }
        scope_signatures = {
            (
                str(row["unit_code"]).strip() or None
                if row.get("unit_code") is not None
                else None,
                _finite(row.get("lsl"), field=f"{parameter} formal LSL"),
                _finite(row.get("usl"), field=f"{parameter} formal USL"),
                _condition(row.get("condition_json"), parameter=parameter),
                _operator(
                    row.get("lower_operator"),
                    limit=row.get("lsl"),
                    allowed=frozenset({">", ">="}),
                    field=f"{parameter} formal lower operator",
                ),
                _operator(
                    row.get("upper_operator"),
                    limit=row.get("usl"),
                    allowed=frozenset({"<", "<="}),
                    field=f"{parameter} formal upper operator",
                ),
            )
            for row in covered
        }
        if any(
            signature[4] not in {None, ">", ">="}
            or signature[5] not in {None, "<", "<="}
            for signature in scope_signatures
        ):
            raise DomainError(
                "ANALYSIS_SPEC_OPERATOR_INVALID",
                f"parameter {parameter} has an unsupported formal Spec operator",
                409,
            )
        if (
            len(scope_ids) != 1
            or len(scope_versions) != 1
            or len(scope_signatures) != 1
        ):
            reasons.append("SPEC_CONTEXT_AMBIGUOUS")
            continue
        spec_ids.update(scope_ids)
        spec_versions.update(scope_versions)
        signatures.update(scope_signatures)

    if len(spec_ids) > 1 or len(spec_versions) > 1 or len(signatures) > 1:
        reasons.append("SPEC_CONTEXT_AMBIGUOUS")
    signature = next(iter(signatures)) if len(signatures) == 1 else None
    if signature is not None and (
        signature[0] != identity_unit or signature[3] != identity_condition
    ):
        reasons.append("SPEC_CONTEXT_AMBIGUOUS")
        signature = None

    lsl = signature[1] if signature is not None else None
    usl = signature[2] if signature is not None else None
    lower_operator = signature[4] if signature is not None else None
    upper_operator = signature[5] if signature is not None else None
    if lsl is not None and usl is not None and lsl > usl:
        raise DomainError(
            "ANALYSIS_SPEC_DIRECTION_INVALID",
            f"parameter {parameter} has reversed formal specification limits",
            409,
        )
    if (
        lsl is not None
        and usl is not None
        and lsl == usl
        and (lower_operator == ">" or upper_operator == "<")
    ):
        raise DomainError(
            "ANALYSIS_SPEC_DIRECTION_INVALID",
            f"parameter {parameter} has an empty formal specification interval",
            409,
        )
    if signature is not None and lsl is None and usl is None:
        reasons.append("FORMAL_SPEC_LIMIT_MISSING")
        signature = None

    normalized_reasons = tuple(dict.fromkeys(reasons))
    if signature is None or normalized_reasons or len(spec_ids) != 1:
        return FormalSpecResolution(
            status="NO_SPEC",
            reason_codes=normalized_reasons or ("FORMAL_RELEASED_SPEC_NOT_FOUND",),
            spec_set_ids=(),
            spec_versions=(),
            unit=None,
            test_condition=None,
            lsl=None,
            usl=None,
            lower_operator=None,
            upper_operator=None,
        )
    return FormalSpecResolution(
        status="RESOLVED",
        reason_codes=(),
        spec_set_ids=tuple(sorted(spec_ids)),
        spec_versions=tuple(sorted(spec_versions)),
        unit=signature[0],
        test_condition=signature[3],
        lsl=lsl,
        usl=usl,
        lower_operator=lower_operator,
        upper_operator=upper_operator,
    )
