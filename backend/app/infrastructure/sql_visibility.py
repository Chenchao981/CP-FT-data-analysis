from __future__ import annotations

from app.domain.auth import Principal


def visibility_parameters(principal: Principal) -> dict[str, object]:
    return {
        "user_id": principal.user_id,
        "is_admin": "SYSTEM_ADMIN" in principal.roles,
    }


def can_manage_sql(*, owner_column: str) -> str:
    return f"(:is_admin=1 OR {owner_column}=:user_id)"


def batch_owner_scope_sql(*, batch_alias: str = "b") -> str:
    """Restrict private batch details to the uploader or a system administrator."""
    return can_manage_sql(owner_column=f"{batch_alias}.owner_user_id")


def batch_read_scope_sql(*, batch_alias: str = "b") -> str:
    return (
        "(:is_admin=1 OR "
        f"{batch_alias}.owner_user_id=:user_id OR "
        f"{batch_alias}.business_domain='PRODUCTION')"
    )


def batch_write_scope_sql(*, batch_alias: str = "b") -> str:
    return batch_owner_scope_sql(batch_alias=batch_alias)


def current_dataset_read_scope_sql(
    *,
    dataset_alias: str = "d",
    version_alias: str = "dv",
    batch_alias: str = "b",
) -> str:
    return (
        "(:is_admin=1 OR "
        f"{dataset_alias}.owner_user_id=:user_id OR ("
        f"{batch_alias}.business_domain='PRODUCTION' AND "
        f"{version_alias}.status='PUBLISHED' AND "
        f"{version_alias}.is_current=1))"
    )


def formal_result_read_scope_sql(
    *, summary_alias: str = "s", batch_alias: str = "b"
) -> str:
    return (
        "(:is_admin=1 OR "
        f"{batch_alias}.owner_user_id=:user_id OR ("
        f"{batch_alias}.business_domain='PRODUCTION' AND EXISTS("
        "SELECT 1 FROM dataset.dataset result_d "
        "JOIN dataset.dataset_version result_dv "
        "ON result_dv.dataset_id=result_d.dataset_id "
        f"WHERE result_d.dataset_id={summary_alias}.dataset_id "
        f"AND result_dv.version_no={summary_alias}.dataset_version_no "
        "AND result_dv.status='PUBLISHED' AND result_dv.is_current=1)))"
    )
