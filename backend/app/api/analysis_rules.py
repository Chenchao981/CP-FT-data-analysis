from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies import require_permission
from app.core.errors import DomainError
from app.domain.analysis_rules import (
    ActivateAnalysisRuleRequest,
    CreateAnalysisRuleSetRequest,
    CreateAnalysisRuleVersionRequest,
    DecideAnalysisRuleRequest,
)
from app.domain.auth import Principal
from app.infrastructure.sql_analysis_rule_service import SqlAnalysisRuleService

router = APIRouter()


def service(request: Request) -> SqlAnalysisRuleService:
    instance = getattr(request.app.state, "analysis_rule_service", None)
    if instance is None:
        raise DomainError(
            "DATABASE_NOT_CONFIGURED",
            "analysis rule governance requires TMS_DATABASE_URL",
            503,
        )
    return instance


@router.get("")
def list_rules(
    request: Request,
    principal: Principal = Depends(require_permission("RULE_GOVERN")),  # noqa: B008
) -> list[dict]:
    return [asdict(item) for item in service(request).list_rule_sets(principal)]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_rule(
    body: CreateAnalysisRuleSetRequest,
    request: Request,
    principal: Principal = Depends(require_permission("RULE_GOVERN")),  # noqa: B008
) -> dict:
    return asdict(service(request).create_rule_set(body, principal))


@router.post("/{rule_code}/versions", status_code=status.HTTP_201_CREATED)
def create_rule_version(
    rule_code: str,
    body: CreateAnalysisRuleVersionRequest,
    request: Request,
    principal: Principal = Depends(require_permission("RULE_GOVERN")),  # noqa: B008
) -> dict:
    return asdict(service(request).create_version(rule_code, body, principal))


@router.get("/{rule_code}/versions")
def list_rule_versions(
    rule_code: str,
    request: Request,
    principal: Principal = Depends(require_permission("RULE_GOVERN")),  # noqa: B008
) -> list[dict]:
    return [
        asdict(item) for item in service(request).list_versions(rule_code, principal)
    ]


@router.post("/versions/{rule_version_id}/decisions")
def decide_rule_version(
    rule_version_id: int,
    body: DecideAnalysisRuleRequest,
    request: Request,
    principal: Principal = Depends(require_permission("RULE_GOVERN")),  # noqa: B008
) -> dict:
    return asdict(service(request).decide(rule_version_id, body, principal))


@router.post("/versions/{rule_version_id}/activations", status_code=201)
def activate_rule_version(
    rule_version_id: int,
    body: ActivateAnalysisRuleRequest,
    request: Request,
    principal: Principal = Depends(require_permission("RULE_GOVERN")),  # noqa: B008
) -> dict:
    return asdict(service(request).activate(rule_version_id, body, principal))


@router.get("/versions/{rule_version_id}")
def get_rule_version(
    rule_version_id: int,
    request: Request,
    principal: Principal = Depends(require_permission("RULE_GOVERN")),  # noqa: B008
) -> dict:
    return asdict(service(request).get_version(rule_version_id, principal))
