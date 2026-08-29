from __future__ import annotations

import re
from datetime import UTC, datetime

from app.core.errors import DomainError
from app.domain.m2_queries import M2PageFilters

_CODE = re.compile(r"^[A-Za-z0-9_.-]+$")


def build_page_filters(
    *,
    page: int,
    page_size: int,
    business_domain: str | None = None,
    test_stage: str | None = None,
    factory_code: str | None = None,
    status: str | None = None,
    product_name: str | None = None,
    lot_id: str | None = None,
    wafer_id: str | None = None,
    import_batch_id: int | None = None,
    cleaner_version: str | None = None,
    owner_login: str | None = None,
    from_utc: datetime | None = None,
    to_utc: datetime | None = None,
    allowed_statuses: frozenset[str],
) -> M2PageFilters:
    domain = _choice(
        business_domain, "business_domain", frozenset({"ENGINEERING", "PRODUCTION"})
    )
    stage = _choice(test_stage, "test_stage", frozenset({"CP", "FT"}))
    factory = _code(factory_code, "factory_code")
    normalized_status = _choice(status, "status", allowed_statuses)
    product = _text(product_name, "product_name")
    lot = _text(lot_id, "lot_id")
    wafer = _text(wafer_id, "wafer_id")
    cleaner = _text(cleaner_version, "cleaner_version")
    owner = _text(owner_login, "owner_login")
    lower = _utc(from_utc, "from_utc")
    upper = _utc(to_utc, "to_utc")
    if lower is not None and upper is not None and lower > upper:
        raise DomainError(
            "TIME_RANGE_INVALID", "from_utc 不能晚于 to_utc", 422
        )
    return M2PageFilters(
        page=page,
        page_size=page_size,
        business_domain=domain,
        test_stage=stage,
        factory_code=factory,
        status=normalized_status,
        product_name=product,
        lot_id=lot,
        wafer_id=wafer,
        import_batch_id=import_batch_id,
        cleaner_version=cleaner,
        owner_login=owner,
        from_utc=lower,
        to_utc=upper,
    )


def _choice(value: str | None, name: str, allowed: frozenset[str]) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized not in allowed:
        raise DomainError(
            "FILTER_VALUE_INVALID",
            f"{name} 不在允许范围内",
            422,
        )
    return normalized


def _code(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if not normalized or not _CODE.fullmatch(normalized):
        raise DomainError(
            "FILTER_VALUE_INVALID", f"{name} 格式无效", 422
        )
    return normalized


def _text(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or any(ord(character) < 32 for character in normalized):
        raise DomainError(
            "FILTER_VALUE_INVALID", f"{name} 格式无效", 422
        )
    return normalized


def _utc(value: datetime | None, name: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainError(
            "FILTER_TIMEZONE_REQUIRED",
            f"{name} 必须包含时区，建议使用 Z",
            422,
        )
    return value.astimezone(UTC).replace(tzinfo=None)
