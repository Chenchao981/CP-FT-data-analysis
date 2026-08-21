from __future__ import annotations

import os

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def main() -> int:
    password = os.environ["TMS_VERIFY_ADMIN_PASSWORD"]
    demo_password = os.getenv("TMS_VERIFY_DEMO_PASSWORD", "DemoUser123!")
    get_settings.cache_clear()
    client = TestClient(create_app())
    admin_login = client.post(
        "/api/v1/auth/login",
        json={"login_name": "admin", "password": password},
    )
    assert admin_login.status_code == 200, admin_login.text
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    me = client.get("/api/v1/auth/me", headers=admin_headers)
    assert me.status_code == 200 and "USER_ADMIN" in me.json()["permissions"]

    users = client.get("/api/v1/auth/users", headers=admin_headers).json()
    demo = next((item for item in users if item["login_name"] == "demo.cp"), None)
    if demo is None:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "login_name": "demo.cp",
                "display_name": "CP演示用户",
                "password": demo_password,
                "department_code": "CP",
            },
        )
        assert registered.status_code == 201, registered.text
        demo = registered.json()
    updated = client.put(
        f"/api/v1/auth/users/{demo['user_id']}",
        headers=admin_headers,
        json={"status": "ACTIVE", "role_codes": ["CP_ENGINEER"], "department_code": "CP"},
    )
    assert updated.status_code == 200, updated.text

    demo_login = client.post(
        "/api/v1/auth/login",
        json={"login_name": "demo.cp", "password": demo_password},
    )
    assert demo_login.status_code == 200, demo_login.text
    demo_headers = {"Authorization": f"Bearer {demo_login.json()['access_token']}"}
    forbidden = client.get("/api/v1/auth/users", headers=demo_headers)
    assert forbidden.status_code == 403, forbidden.text
    datasets = client.get("/api/v1/datasets", headers=demo_headers)
    assert datasets.status_code == 200, datasets.text
    print(
        "AUTH_RBAC_OK",
        f"admin={me.json()['login_name']}",
        f"demo_roles={','.join(demo_login.json()['user']['roles'])}",
        f"demo_visible_datasets={len(datasets.json())}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
