from __future__ import annotations

import argparse
import os

from sqlalchemy import text

from app.core.security import hash_password
from app.infrastructure.database import get_engine


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or refresh the first TMS administrator")
    parser.add_argument("--login", default="admin")
    parser.add_argument("--display-name", default="系统管理员")
    parser.add_argument("--department", default="IT")
    args = parser.parse_args()
    password = os.getenv("TMS_BOOTSTRAP_ADMIN_PASSWORD")
    if not password or len(password) < 8:
        raise RuntimeError("TMS_BOOTSTRAP_ADMIN_PASSWORD must contain at least 8 characters")
    encoded = hash_password(password)
    with get_engine().begin() as connection:
        user_id = connection.execute(
            text("SELECT user_id FROM iam.app_user WHERE login_name=:login"),
            {"login": args.login.lower()},
        ).scalar_one_or_none()
        if user_id is None:
            user_id = connection.execute(
                text(
                    "INSERT iam.app_user(login_name,display_name,department_code,identity_provider,"
                    "password_hash,status) OUTPUT INSERTED.user_id VALUES("
                    ":login,:display_name,:department,'LOCAL',:password_hash,'ACTIVE')"
                ),
                {
                    "login": args.login.lower(),
                    "display_name": args.display_name,
                    "department": args.department,
                    "password_hash": encoded,
                },
            ).scalar_one()
        else:
            connection.execute(
                text(
                    "UPDATE iam.app_user SET display_name=:display_name,department_code=:department,"
                    "password_hash=:password_hash,status='ACTIVE',updated_at_utc=SYSUTCDATETIME() "
                    "WHERE user_id=:user_id"
                ),
                {
                    "user_id": user_id,
                    "display_name": args.display_name,
                    "department": args.department,
                    "password_hash": encoded,
                },
            )
        connection.execute(
            text(
                "INSERT iam.user_role(user_id,role_id,granted_by) "
                "SELECT :user_id,r.role_id,:user_id FROM iam.role r WHERE r.role_code='SYSTEM_ADMIN' "
                "AND NOT EXISTS(SELECT 1 FROM iam.user_role ur WHERE ur.user_id=:user_id AND ur.role_id=r.role_id)"
            ),
            {"user_id": int(user_id)},
        )
    print(f"ADMIN_READY user_id={int(user_id)} login={args.login.lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
