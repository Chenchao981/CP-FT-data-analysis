from __future__ import annotations

import os

from app.core.security import hash_password
from app.infrastructure.database import get_engine
from sqlalchemy import bindparam, text

ACCOUNTS = (
    {
        "login": "admin",
        "display_name": "系统超级管理员",
        "department": "IT",
        "role": "SYSTEM_ADMIN",
        "password_env": "TMS_BOOTSTRAP_ADMIN_PASSWORD",
    },
    {
        "login": "domain_admin",
        "display_name": "数据域管理员",
        "department": "IT",
        "role": "DATA_DOMAIN_ADMIN",
        "password_env": "TMS_BOOTSTRAP_DOMAIN_ADMIN_PASSWORD",
    },
)

DOMAIN_ADMIN_PERMISSIONS = (
    "TASK_CREATE",
    "TASK_RETRY",
    "DATASET_READ",
    "DATASET_PUBLISH",
    "ANALYSIS_RUN",
    "EXPORT_DATA",
    "FORMAT_GOVERN",
    "RULE_GOVERN",
    "DQ_WAIVE_ERROR",
    "AUDIT_READ",
    "MANAGEMENT_READ",
    "DATA_DOMAIN_ADMIN",
)


def _password(account: dict[str, str]) -> str:
    value = os.getenv(account["password_env"])
    if not value or len(value) < 8:
        raise RuntimeError(f"{account['password_env']} must contain at least 8 characters")
    return value


def main() -> int:
    passwords = {account["login"]: _password(account) for account in ACCOUNTS}
    ready: list[tuple[int, str, str]] = []
    with get_engine().begin() as connection:
        connection.execute(
            text(
                "IF NOT EXISTS(SELECT 1 FROM iam.role WHERE role_code='DATA_DOMAIN_ADMIN') "
                "INSERT iam.role(role_code,role_name,active) "
                "VALUES('DATA_DOMAIN_ADMIN',N'数据域管理员',1) ELSE "
                "UPDATE iam.role SET role_name=N'数据域管理员',active=1 "
                "WHERE role_code='DATA_DOMAIN_ADMIN'"
            )
        )
        connection.execute(
            text(
                "INSERT iam.role_permission(role_id,permission_id) "
                "SELECT r.role_id,p.permission_id FROM iam.role r JOIN iam.permission p "
                "ON p.permission_code IN :permission_codes "
                "WHERE r.role_code='DATA_DOMAIN_ADMIN' AND NOT EXISTS("
                "SELECT 1 FROM iam.role_permission rp WHERE rp.role_id=r.role_id "
                "AND rp.permission_id=p.permission_id)"
            ).bindparams(bindparam("permission_codes", expanding=True)),
            {"permission_codes": DOMAIN_ADMIN_PERMISSIONS},
        )
        for account in ACCOUNTS:
            user_id = connection.execute(
                text("SELECT user_id FROM iam.app_user WHERE login_name=:login"),
                {"login": account["login"]},
            ).scalar_one_or_none()
            values = {
                "login": account["login"],
                "display_name": account["display_name"],
                "department": account["department"],
                "password_hash": hash_password(passwords[account["login"]]),
            }
            if user_id is None:
                user_id = connection.execute(
                    text(
                        "INSERT iam.app_user(login_name,display_name,department_code,"
                        "identity_provider,password_hash,status) OUTPUT INSERTED.user_id "
                        "VALUES(:login,:display_name,:department,'LOCAL',:password_hash,'ACTIVE')"
                    ),
                    values,
                ).scalar_one()
            else:
                connection.execute(
                    text(
                        "UPDATE iam.app_user SET display_name=:display_name,"
                        "department_code=:department,password_hash=:password_hash,"
                        "status='ACTIVE',updated_at_utc=SYSUTCDATETIME() WHERE user_id=:user_id"
                    ),
                    values | {"user_id": int(user_id)},
                )
                connection.execute(
                    text("DELETE iam.auth_session WHERE user_id=:user_id"),
                    {"user_id": int(user_id)},
                )
            role_id = connection.execute(
                text("SELECT role_id FROM iam.role WHERE role_code=:role AND active=1"),
                {"role": account["role"]},
            ).scalar_one()
            connection.execute(
                text("DELETE iam.user_role WHERE user_id=:user_id"),
                {"user_id": int(user_id)},
            )
            connection.execute(
                text(
                    "INSERT iam.user_role(user_id,role_id,granted_by) "
                    "VALUES(:user_id,:role_id,:user_id)"
                ),
                {"user_id": int(user_id), "role_id": int(role_id)},
            )
            ready.append((int(user_id), account["login"], account["role"]))
    for user_id, login, role in ready:
        print(f"DEVELOPMENT_ACCOUNT_READY user_id={user_id} login={login} role={role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
