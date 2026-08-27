from __future__ import annotations

from pathlib import Path

import pytest
from app.core.errors import DomainError
from app.domain.quick_capacity import QuickCapacityPolicy


def _policy(tmp_path: Path) -> QuickCapacityPolicy:
    return QuickCapacityPolicy(
        global_capacity_bytes=10_000,
        user_capacity_bytes=5_000,
        minimum_free_bytes=100,
        reserve_ratio=0.5,
        reserve_overhead_bytes=10,
        work_root=tmp_path,
    )


def test_quick_capacity_estimates_spool_reservation() -> None:
    policy = _policy(Path.cwd())
    assert policy.reservation_for(1_001) == 511


def test_quick_capacity_rejects_user_and_global_overage(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    with pytest.raises(DomainError) as user_error:
        policy.ensure_quota(
            global_used_bytes=1_000,
            user_used_bytes=4_800,
            reservation_bytes=300,
        )
    assert user_error.value.code == "QUICK_USER_CAPACITY_EXCEEDED"

    with pytest.raises(DomainError) as global_error:
        policy.ensure_quota(
            global_used_bytes=9_800,
            user_used_bytes=100,
            reservation_bytes=300,
        )
    assert global_error.value.code == "QUICK_GLOBAL_CAPACITY_EXCEEDED"
