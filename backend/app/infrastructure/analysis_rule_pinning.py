from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.core.errors import DomainError
from app.domain.analysis_rule_pinning import AnalysisRuleRequirement
from app.domain.saved_analyses import SavedAnalysisRuleContext


class ApprovedRuleParameterResolver(Protocol):
    def __call__(
        self,
        *,
        rule_code: str,
        version_code: str,
        test_stage: str,
        expected_algorithm_code: str,
        supplier_id: int | None = None,
        product_id: int | None = None,
        parameter: str | None = None,
    ) -> dict[str, Any]: ...


def validate_required_analysis_rules(
    requirements: Sequence[AnalysisRuleRequirement],
    dataset_rows: Sequence[Mapping[str, Any]],
    resolver: ApprovedRuleParameterResolver,
) -> tuple[str, ...]:
    """Validate every exact Rule against every selected Dataset/parameter scope."""

    identities: list[str] = []
    for requirement in requirements:
        resolved_contract: str | None = None
        visited_scopes: set[tuple[str, int | None, int | None, str | None]] = set()
        for row in dataset_rows:
            supplier_id = (
                int(row["supplier_id"]) if row.get("supplier_id") is not None else None
            )
            product_id = (
                int(row["product_id"]) if row.get("product_id") is not None else None
            )
            for parameter in requirement.parameters:
                scope = (
                    str(row["test_stage"]),
                    supplier_id,
                    product_id,
                    parameter,
                )
                if scope in visited_scopes:
                    continue
                visited_scopes.add(scope)
                parameters = resolver(
                    rule_code=requirement.rule_code,
                    version_code=requirement.version_code,
                    test_stage=scope[0],
                    expected_algorithm_code=requirement.algorithm_code,
                    supplier_id=supplier_id,
                    product_id=product_id,
                    parameter=parameter,
                )
                if not isinstance(parameters, dict):
                    raise DomainError(
                        "ANALYSIS_RULE_CONTRACT_INVALID",
                        "approved analysis Rule parameters must be one JSON object",
                        409,
                    )
                try:
                    canonical = json.dumps(
                        parameters,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                except (TypeError, ValueError) as exc:
                    raise DomainError(
                        "ANALYSIS_RULE_CONTRACT_INVALID",
                        "approved analysis Rule parameters are not canonical JSON",
                        409,
                    ) from exc
                if resolved_contract is None:
                    resolved_contract = canonical
                elif resolved_contract != canonical:
                    raise DomainError(
                        "ANALYSIS_RULE_CONTRACT_INVALID",
                        "one exact Rule resolves to inconsistent parameters across the selected scopes",
                        409,
                    )
        if resolved_contract is None:
            raise DomainError(
                "ANALYSIS_RULE_NOT_APPROVED",
                "selected analysis Rule has no approved Dataset/parameter scope",
                409,
            )
        identities.append(requirement.identity)
    return tuple(sorted(set(identities)))


def verified_merged_rule_context(
    *,
    current: SavedAnalysisRuleContext,
    requested: SavedAnalysisRuleContext,
    required_identities: Sequence[str],
    stale_code: str,
    stale_message: str,
) -> SavedAnalysisRuleContext:
    """Verify the client snapshot, then merge server-derived required Rule refs.

    Clients may send either the base current context or the already-merged form.
    No third form is accepted, so a client cannot inject an unvalidated Rule ref.
    """

    try:
        effective = SavedAnalysisRuleContext(
            spec_versions=current.spec_versions,
            bin_mapping_versions=current.bin_mapping_versions,
            evaluation_rule_versions=sorted(
                set(current.evaluation_rule_versions).union(required_identities)
            ),
        )
    except ValueError as exc:
        raise DomainError(
            "ANALYSIS_RULE_CONTEXT_INVALID",
            "the frozen analysis Rule context exceeds the supported contract",
            409,
        ) from exc
    requested_context = _hashable_context(requested)
    if requested_context not in {
        _hashable_context(current),
        _hashable_context(effective),
    }:
        raise DomainError(stale_code, stale_message, 409)
    return effective


def _hashable_context(context: SavedAnalysisRuleContext) -> tuple[tuple[str, ...], ...]:
    return (
        tuple(context.spec_versions),
        tuple(context.bin_mapping_versions),
        tuple(context.evaluation_rule_versions),
    )
