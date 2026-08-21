from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.core.errors import DomainError
from app.domain.auth import (
    Principal,
    RegisterRequest,
    UserAdminUpdateRequest,
    UserRecord,
)


class SqlAuthService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def register(self, request: RegisterRequest, password_hash: str) -> UserRecord:
        try:
            with self._engine.begin() as connection:
                user_id = int(
                    connection.execute(
                        text(
                            "INSERT iam.app_user(login_name,display_name,email,department_code,"
                            "identity_provider,password_hash,status) OUTPUT INSERTED.user_id VALUES("
                            ":login_name,:display_name,:email,:department_code,'LOCAL',:password_hash,'PENDING')"
                        ),
                        {
                            **request.model_dump(exclude={"password"}),
                            "password_hash": password_hash,
                        },
                    ).scalar_one()
                )
            return self._user(user_id)
        except IntegrityError as exc:
            raise DomainError("USER_ALREADY_EXISTS", "登录名或邮箱已存在", 409) from exc

    def password_hash_for_login(self, login_name: str) -> tuple[int, str, str]:
        normalized = login_name.strip().lower()
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT user_id,password_hash,status FROM iam.app_user "
                    "WHERE login_name=:login_name AND identity_provider='LOCAL'"
                ),
                {"login_name": normalized},
            ).mappings().one_or_none()
        if row is None:
            self.record_login(normalized, None, "NOT_FOUND")
            raise DomainError("LOGIN_FAILED", "用户名或密码不正确", 401)
        status = str(row["status"])
        if status != "ACTIVE":
            self.record_login(normalized, int(row["user_id"]), "LOCKED" if status == "LOCKED" else "NOT_ACTIVE")
            message = "账户正在等待管理员启用" if status == "PENDING" else "账户当前不可登录"
            raise DomainError("USER_NOT_ACTIVE", message, 403)
        return int(row["user_id"]), str(row["password_hash"]), normalized

    def record_login(
        self,
        login_name: str,
        user_id: int | None,
        outcome: str,
        *,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT iam.login_audit(login_name,user_id,outcome,client_ip,user_agent) "
                    "VALUES(:login_name,:user_id,:outcome,:client_ip,:user_agent)"
                ),
                {
                    "login_name": login_name,
                    "user_id": user_id,
                    "outcome": outcome,
                    "client_ip": client_ip,
                    "user_agent": user_agent,
                },
            )
            if user_id is not None and outcome == "BAD_PASSWORD":
                connection.execute(
                    text(
                        "UPDATE iam.app_user SET failed_login_count=failed_login_count+1,"
                        "updated_at_utc=SYSUTCDATETIME() WHERE user_id=:user_id"
                    ),
                    {"user_id": user_id},
                )
            elif user_id is not None and outcome == "SUCCESS":
                connection.execute(
                    text(
                        "UPDATE iam.app_user SET failed_login_count=0,last_login_at_utc=SYSUTCDATETIME(),"
                        "updated_at_utc=SYSUTCDATETIME() WHERE user_id=:user_id"
                    ),
                    {"user_id": user_id},
                )

    def principal_for_user(self, user_id: int) -> Principal:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT user_id,login_name,display_name,department_code,status "
                    "FROM iam.app_user WHERE user_id=:user_id"
                ),
                {"user_id": user_id},
            ).mappings().one_or_none()
            if row is None or row["status"] != "ACTIVE":
                raise DomainError("USER_NOT_ACTIVE", "用户不存在或已停用", 401)
            roles = tuple(
                str(item[0])
                for item in connection.execute(
                    text(
                        "SELECT r.role_code FROM iam.user_role ur JOIN iam.role r ON r.role_id=ur.role_id "
                        "WHERE ur.user_id=:user_id AND r.active=1 ORDER BY r.role_code"
                    ),
                    {"user_id": user_id},
                ).all()
            )
            permissions = frozenset(
                str(item[0])
                for item in connection.execute(
                    text(
                        "SELECT DISTINCT p.permission_code FROM iam.user_role ur "
                        "JOIN iam.role r ON r.role_id=ur.role_id AND r.active=1 "
                        "JOIN iam.role_permission rp ON rp.role_id=r.role_id "
                        "JOIN iam.permission p ON p.permission_id=rp.permission_id "
                        "WHERE ur.user_id=:user_id"
                    ),
                    {"user_id": user_id},
                ).all()
            )
        return Principal(
            user_id=int(row["user_id"]),
            login_name=str(row["login_name"]),
            display_name=str(row["display_name"]),
            department_code=str(row["department_code"]) if row["department_code"] else None,
            roles=roles,
            permissions=permissions,
        )

    def create_session(
        self,
        user_id: int,
        token_jti: str,
        expires_at_utc: datetime,
        **metadata,
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT iam.auth_session(user_id,token_jti,expires_at_utc,client_ip,user_agent) "
                    "VALUES(:user_id,:token_jti,:expires_at_utc,:client_ip,:user_agent)"
                ),
                {
                    "user_id": user_id,
                    "token_jti": token_jti,
                    "expires_at_utc": expires_at_utc.replace(tzinfo=None),
                    "client_ip": metadata.get("client_ip"),
                    "user_agent": metadata.get("user_agent"),
                },
            )

    def principal_for_session(self, token_jti: str) -> Principal:
        with self._engine.connect() as connection:
            user_id = connection.execute(
                text(
                    "SELECT user_id FROM iam.auth_session WHERE token_jti=:token_jti "
                    "AND revoked_at_utc IS NULL AND expires_at_utc>SYSUTCDATETIME()"
                ),
                {"token_jti": token_jti},
            ).scalar_one_or_none()
        if user_id is None:
            raise DomainError("AUTH_SESSION_EXPIRED", "登录状态已失效，请重新登录", 401)
        return self.principal_for_user(int(user_id))

    def revoke_session(self, token_jti: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE iam.auth_session SET revoked_at_utc=SYSUTCDATETIME() "
                    "WHERE token_jti=:token_jti AND revoked_at_utc IS NULL"
                ),
                {"token_jti": token_jti},
            )

    def list_users(self) -> tuple[UserRecord, ...]:
        with self._engine.connect() as connection:
            ids = [int(row[0]) for row in connection.execute(text("SELECT user_id FROM iam.app_user ORDER BY user_id"))]
        return tuple(self._user(user_id) for user_id in ids)

    def list_roles(self) -> tuple[dict[str, str], ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text("SELECT role_code,role_name FROM iam.role WHERE active=1 ORDER BY role_code")
            ).mappings().all()
        return tuple(
            {"role_code": str(row["role_code"]), "role_name": str(row["role_name"])}
            for row in rows
        )

    def update_user(
        self, user_id: int, request: UserAdminUpdateRequest, actor_id: int
    ) -> UserRecord:
        with self._engine.begin() as connection:
            valid_roles = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    text("SELECT role_code,role_id FROM iam.role WHERE active=1")
                ).all()
            }
            unknown = sorted(set(request.role_codes) - valid_roles.keys())
            if unknown:
                raise DomainError("ROLE_NOT_FOUND", f"未知角色：{', '.join(unknown)}", 422)
            updated = connection.execute(
                text(
                    "UPDATE iam.app_user SET status=:status,department_code=:department_code,"
                    "updated_at_utc=SYSUTCDATETIME() WHERE user_id=:user_id"
                ),
                {"user_id": user_id, "status": request.status, "department_code": request.department_code},
            ).rowcount
            if not updated:
                raise DomainError("USER_NOT_FOUND", "用户不存在", 404)
            connection.execute(text("DELETE iam.user_role WHERE user_id=:user_id"), {"user_id": user_id})
            if request.role_codes:
                connection.execute(
                    text(
                        "INSERT iam.user_role(user_id,role_id,granted_by) "
                        "VALUES(:user_id,:role_id,:actor_id)"
                    ),
                    [
                        {"user_id": user_id, "role_id": valid_roles[code], "actor_id": actor_id}
                        for code in sorted(set(request.role_codes))
                    ],
                )
        return self._user(user_id)

    def _user(self, user_id: int) -> UserRecord:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT user_id,login_name,display_name,email,department_code,status,"
                    "created_at_utc,last_login_at_utc FROM iam.app_user WHERE user_id=:user_id"
                ),
                {"user_id": user_id},
            ).mappings().one_or_none()
            if row is None:
                raise DomainError("USER_NOT_FOUND", "用户不存在", 404)
            roles = tuple(
                str(item[0])
                for item in connection.execute(
                    text(
                        "SELECT r.role_code FROM iam.user_role ur JOIN iam.role r ON r.role_id=ur.role_id "
                        "WHERE ur.user_id=:user_id ORDER BY r.role_code"
                    ),
                    {"user_id": user_id},
                ).all()
            )
            permissions = tuple(
                str(item[0])
                for item in connection.execute(
                    text(
                        "SELECT DISTINCT p.permission_code FROM iam.user_role ur "
                        "JOIN iam.role_permission rp ON rp.role_id=ur.role_id "
                        "JOIN iam.permission p ON p.permission_id=rp.permission_id "
                        "WHERE ur.user_id=:user_id ORDER BY p.permission_code"
                    ),
                    {"user_id": user_id},
                ).all()
            )
        return UserRecord(
            user_id=int(row["user_id"]),
            login_name=str(row["login_name"]),
            display_name=str(row["display_name"]),
            email=str(row["email"]) if row["email"] else None,
            department_code=str(row["department_code"]) if row["department_code"] else None,
            status=str(row["status"]),
            roles=roles,
            permissions=permissions,
            created_at_utc=_iso(row["created_at_utc"]),
            last_login_at_utc=_iso(row["last_login_at_utc"]) if row["last_login_at_utc"] else None,
        )


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat()
    return str(value)
