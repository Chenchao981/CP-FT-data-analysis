from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.api.data_domains import router
from app.api.dependencies import current_principal
from app.core.errors import DomainError
from app.core.exception_handlers import domain_error_handler
from app.domain.auth import Principal
from app.domain.data_domains import (
    DataDomainGrantRecord,
    DataDomainRecord,
    GrantableUserRecord,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _principal(*permissions: str) -> Principal:
    return Principal(
        user_id=7,
        login_name="domain.admin" if "DATA_DOMAIN_ADMIN" in permissions else "engineer",
        display_name="Domain Admin" if "DATA_DOMAIN_ADMIN" in permissions else "Engineer",
        roles=("DATA_DOMAIN_ADMIN",) if "DATA_DOMAIN_ADMIN" in permissions else ("BUSINESS_USER",),
        permissions=frozenset(permissions),
    )


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.grant_record = DataDomainGrantRecord(
            user_id=23,
            login_name="cp.user",
            display_name="CP User",
            expires_at_utc="2026-12-31T00:00:00Z",
            granted_at_utc="2026-09-01T00:00:00Z",
            reason="approved source access",
        )
        self.domain = DataDomainRecord(
            data_domain_id=11,
            domain_code="HUAHONG_CP",
            domain_name="华虹 CP",
            test_stage="CP",
            factory_code="HUAHONG",
            active=True,
            grant_expires_at_utc="2026-12-31T00:00:00Z",
            grants=(self.grant_record,),
        )

    def list_for_principal(self, principal):
        self.calls.append(("mine", principal.user_id))
        return (
            DataDomainRecord(
                data_domain_id=self.domain.data_domain_id,
                domain_code=self.domain.domain_code,
                domain_name=self.domain.domain_name,
                test_stage=self.domain.test_stage,
                factory_code=self.domain.factory_code,
                active=self.domain.active,
                grant_expires_at_utc=self.domain.grant_expires_at_utc,
            ),
        )

    def list_admin(self):
        self.calls.append(("admin-list", None))
        return (self.domain,)

    def list_grantable_users(self):
        self.calls.append(("grantable-users", None))
        return (
            GrantableUserRecord(
                user_id=23,
                login_name="cp.user",
                display_name="CP User",
            ),
        )

    def create(self, request, principal):
        self.calls.append(("create", (request.domain_code, principal.user_id)))
        return self.domain

    def update(self, data_domain_id, request, principal):
        self.calls.append(
            ("update", (data_domain_id, request.active, principal.user_id))
        )
        return self.domain

    def grant(self, data_domain_id, request, principal):
        self.calls.append(
            ("grant", (data_domain_id, request.user_id, principal.user_id))
        )
        return self.grant_record

    def revoke(self, data_domain_id, user_id, principal):
        self.calls.append(("revoke", (data_domain_id, user_id, principal.user_id)))


def _client(principal: Principal) -> tuple[TestClient, _Service]:
    application = FastAPI()
    application.add_exception_handler(DomainError, domain_error_handler)
    application.include_router(router, prefix="/api/v1")
    instance = _Service()
    application.state.data_domain_service = instance
    application.dependency_overrides[current_principal] = lambda: principal
    return TestClient(application), instance


def test_current_user_lists_only_service_resolved_grants() -> None:
    client, service = _client(_principal("DATASET_READ"))

    response = client.get("/api/v1/data-domains")

    assert response.status_code == 200
    assert response.json()[0] == {
        "data_domain_id": 11,
        "domain_code": "HUAHONG_CP",
        "domain_name": "华虹 CP",
        "test_stage": "CP",
        "factory_code": "HUAHONG",
        "active": True,
        "grant_expires_at_utc": "2026-12-31T00:00:00Z",
        "grants": [],
    }
    assert service.calls == [("mine", 7)]


def test_business_user_cannot_reach_data_domain_control_plane() -> None:
    client, service = _client(_principal("DATASET_READ"))

    response = client.get("/api/v1/admin/data-domains")

    assert response.status_code == 403
    assert service.calls == []


def test_grantable_users_requires_domain_admin_without_user_admin() -> None:
    admin_client, admin_service = _client(_principal("DATA_DOMAIN_ADMIN"))
    business_client, business_service = _client(_principal("DATASET_READ"))

    allowed = admin_client.get("/api/v1/admin/data-domains/grantable-users")
    denied = business_client.get("/api/v1/admin/data-domains/grantable-users")

    assert allowed.status_code == 200
    assert allowed.json() == [
        {
            "user_id": 23,
            "login_name": "cp.user",
            "display_name": "CP User",
        }
    ]
    assert admin_service.calls == [("grantable-users", None)]
    assert denied.status_code == 403
    assert business_service.calls == []


def test_admin_list_includes_current_grants_and_mutations_use_fixed_routes() -> None:
    client, service = _client(_principal("DATA_DOMAIN_ADMIN"))
    expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()

    listed = client.get("/api/v1/admin/data-domains")
    created = client.post(
        "/api/v1/admin/data-domains",
        json={
            "domain_code": "huahong_cp",
            "domain_name": "华虹 CP",
            "test_stage": "CP",
            "factory_code": "huahong",
            "active": True,
        },
    )
    granted = client.post(
        "/api/v1/admin/data-domains/11/grants",
        json={
            "user_id": 23,
            "expires_at_utc": expires,
            "reason": "approved source access",
        },
    )
    revoked = client.delete("/api/v1/admin/data-domains/11/grants/23")

    assert listed.status_code == 200
    assert listed.json()[0]["grants"][0]["login_name"] == "cp.user"
    assert created.status_code == 201
    assert granted.status_code == 201
    assert revoked.status_code == 204
    assert ("create", ("HUAHONG_CP", 7)) in service.calls
    assert ("grant", (11, 23, 7)) in service.calls
    assert ("revoke", (11, 23, 7)) in service.calls
