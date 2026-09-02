from __future__ import annotations

from app.domain.auth import Principal, has_global_data_access


def visibility_parameters(principal: Principal) -> dict[str, object]:
    """Bind row-level authorization parameters.

    ``is_admin`` is the common development-time global data switch for
    SYSTEM_ADMIN and DATA_DOMAIN_ADMIN. DATA_BREAK_GLASS remains compatible
    with the future security-hardening design.
    """

    break_glass = principal.can("DATA_BREAK_GLASS")
    return {
        "user_id": principal.user_id,
        "is_admin": has_global_data_access(principal),
        "has_data_break_glass": break_glass,
    }


def _batch_columns(batch_alias: str) -> tuple[str, str, str]:
    return (
        f"{batch_alias}.access_scope",
        f"{batch_alias}.owner_user_id",
        f"{batch_alias}.data_domain_id",
    )


def _dataset_columns(dataset_alias: str) -> tuple[str, str, str]:
    return (
        f"{dataset_alias}.access_scope",
        f"{dataset_alias}.owner_user_id",
        f"{dataset_alias}.data_domain_id",
    )


def domain_grant_exists_sql(
    *,
    data_domain_column: str,
    user_expression: str = ":user_id",
    lock_authorization_rows: bool = False,
) -> str:
    lock_hint = " WITH (UPDLOCK,HOLDLOCK)" if lock_authorization_rows else ""
    return (
        "EXISTS(SELECT 1 FROM iam.data_domain_grant access_grant"
        + lock_hint
        + " JOIN iam.data_domain access_domain"
        + lock_hint
        + " "
        "ON access_domain.data_domain_id=access_grant.data_domain_id "
        f"WHERE access_grant.data_domain_id={data_domain_column} "
        f"AND access_grant.user_id={user_expression} "
        "AND access_grant.status='ACTIVE' AND access_domain.active=1 "
        "AND (access_grant.expires_at_utc IS NULL "
        "OR access_grant.expires_at_utc>SYSUTCDATETIME()))"
    )


def data_read_scope_sql(
    *, access_scope_column: str, owner_column: str, data_domain_column: str
) -> str:
    return (
        "(:is_admin=1 OR "
        f"({access_scope_column}='PERSONAL' AND {owner_column}=:user_id) OR "
        f"({access_scope_column}='DOMAIN' AND "
        + domain_grant_exists_sql(data_domain_column=data_domain_column)
        + "))"
    )


def can_manage_sql(*, owner_column: str, access_scope_column: str | None = None) -> str:
    """Allow global administrators or the human owner of PERSONAL data."""

    scope_column = access_scope_column
    if scope_column is None:
        prefix, separator, _name = owner_column.rpartition(".")
        if not separator:
            raise ValueError("access_scope_column is required for unqualified owner")
        scope_column = f"{prefix}.access_scope"
    return f"(:is_admin=1 OR ({scope_column}='PERSONAL' AND {owner_column}=:user_id))"


def batch_owner_scope_sql(*, batch_alias: str = "b") -> str:
    """Restrict mutation and raw-source access to a PERSONAL owner."""

    access_scope, owner, _domain = _batch_columns(batch_alias)
    return can_manage_sql(owner_column=owner, access_scope_column=access_scope)


def batch_read_scope_sql(*, batch_alias: str = "b") -> str:
    access_scope, owner, domain = _batch_columns(batch_alias)
    return data_read_scope_sql(
        access_scope_column=access_scope,
        owner_column=owner,
        data_domain_column=domain,
    )


def batch_write_scope_sql(*, batch_alias: str = "b") -> str:
    return batch_owner_scope_sql(batch_alias=batch_alias)


def quick_read_scope_sql(*, session_alias: str = "ws") -> str:
    """Quick result visibility for administrators, owners, and domain members."""

    return (
        f"(:is_admin=1 OR ({session_alias}.access_scope='PERSONAL' "
        f"AND {session_alias}.owner_user_id=:user_id) OR "
        f"({session_alias}.access_scope='DOMAIN' AND "
        + domain_grant_exists_sql(data_domain_column=f"{session_alias}.data_domain_id")
        + "))"
    )


def quick_write_scope_sql(
    *, session_alias: str = "ws", lock_authorization_rows: bool = False
) -> str:
    """Allow global administrators or the authorized Quick requester."""

    return (
        f"(:is_admin=1 OR ({session_alias}.owner_user_id=:user_id AND ("
        f"({session_alias}.access_scope='PERSONAL') OR "
        f"({session_alias}.access_scope='DOMAIN' AND "
        + domain_grant_exists_sql(
            data_domain_column=f"{session_alias}.data_domain_id",
            lock_authorization_rows=lock_authorization_rows,
        )
        + "))))"
    )


def quick_execution_authorized_sql(*, session_alias: str = "s") -> str:
    """Worker-side authorization for the original Quick Analysis requester."""

    return (
        f"(({session_alias}.access_scope='PERSONAL') OR "
        f"({session_alias}.access_scope='DOMAIN' AND "
        + domain_grant_exists_sql(
            data_domain_column=f"{session_alias}.data_domain_id",
            user_expression=f"{session_alias}.owner_user_id",
        )
        + "))"
    )


def current_dataset_read_scope_sql(
    *,
    dataset_alias: str = "d",
    version_alias: str = "dv",
    batch_alias: str = "b",
) -> str:
    del batch_alias  # Kept for call-site compatibility; Dataset owns its ACL.
    access_scope, owner, domain = _dataset_columns(dataset_alias)
    return (
        "(:is_admin=1 OR "
        f"({access_scope}='PERSONAL' AND {owner}=:user_id) OR ("
        f"{access_scope}='DOMAIN' AND "
        + domain_grant_exists_sql(data_domain_column=domain)
        + " AND "
        f"{version_alias}.status='PUBLISHED' AND "
        f"{version_alias}.is_current=1))"
    )


def formal_result_read_scope_sql(
    *, summary_alias: str = "s", batch_alias: str = "b"
) -> str:
    access_scope, owner, domain = _batch_columns(batch_alias)
    return (
        "(:is_admin=1 OR "
        f"({access_scope}='PERSONAL' AND {owner}=:user_id) OR ("
        f"{access_scope}='DOMAIN' AND "
        + domain_grant_exists_sql(data_domain_column=domain)
        + " AND EXISTS("
        "SELECT 1 FROM dataset.dataset result_d "
        "JOIN dataset.dataset_version result_dv "
        "ON result_dv.dataset_id=result_d.dataset_id "
        f"WHERE result_d.dataset_id={summary_alias}.dataset_id "
        f"AND result_dv.version_no={summary_alias}.dataset_version_no "
        "AND result_d.access_scope='DOMAIN' "
        f"AND result_d.data_domain_id={domain} "
        "AND result_dv.status='PUBLISHED' AND result_dv.is_current=1)))"
    )
