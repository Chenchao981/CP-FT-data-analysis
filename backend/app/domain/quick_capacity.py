from __future__ import annotations

import math
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import DomainError


@dataclass(frozen=True, slots=True)
class QuickCapacityPolicy:
    global_capacity_bytes: int
    user_capacity_bytes: int
    minimum_free_bytes: int
    reserve_ratio: float
    reserve_overhead_bytes: int
    work_root: Path

    @classmethod
    def from_environment(cls) -> QuickCapacityPolicy:
        policy = cls(
            global_capacity_bytes=int(
                os.getenv("TMS_QUICK_GLOBAL_CAPACITY_BYTES", str(100 * 1024**3))
            ),
            user_capacity_bytes=int(
                os.getenv("TMS_QUICK_USER_CAPACITY_BYTES", str(20 * 1024**3))
            ),
            minimum_free_bytes=int(
                os.getenv("TMS_QUICK_MIN_FREE_BYTES", str(10 * 1024**3))
            ),
            reserve_ratio=float(os.getenv("TMS_QUICK_RESERVE_RATIO", "0.5")),
            reserve_overhead_bytes=int(
                os.getenv("TMS_QUICK_RESERVE_OVERHEAD_BYTES", str(64 * 1024**2))
            ),
            work_root=Path(
                os.getenv(
                    "TMS_QUICK_WORK_ROOT", r"F:\CP-FT数据分析\data\workspace"
                )
            ).resolve(),
        )
        policy.validate()
        return policy

    def validate(self) -> None:
        if self.global_capacity_bytes < 1:
            raise RuntimeError("TMS_QUICK_GLOBAL_CAPACITY_BYTES must be positive")
        if self.user_capacity_bytes < 1:
            raise RuntimeError("TMS_QUICK_USER_CAPACITY_BYTES must be positive")
        if self.user_capacity_bytes > self.global_capacity_bytes:
            raise RuntimeError(
                "TMS_QUICK_USER_CAPACITY_BYTES cannot exceed the global capacity"
            )
        if self.minimum_free_bytes < 0:
            raise RuntimeError("TMS_QUICK_MIN_FREE_BYTES cannot be negative")
        if not 0 < self.reserve_ratio <= 2:
            raise RuntimeError("TMS_QUICK_RESERVE_RATIO must be in (0, 2]")
        if self.reserve_overhead_bytes < 0:
            raise RuntimeError("TMS_QUICK_RESERVE_OVERHEAD_BYTES cannot be negative")

    def reservation_for(self, source_total_bytes: int) -> int:
        if source_total_bytes < 0:
            raise ValueError("source_total_bytes cannot be negative")
        return math.ceil(source_total_bytes * self.reserve_ratio) + self.reserve_overhead_bytes

    def reservation_for_local_result(self, max_output_bytes: int) -> int:
        """Reserve only server-side result capacity for a Local Agent receipt."""
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        return max_output_bytes + self.reserve_overhead_bytes

    def ensure_filesystem_capacity(self, reservation_bytes: int) -> None:
        probe = self.work_root
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        free_bytes = shutil.disk_usage(probe).free
        required = reservation_bytes + self.minimum_free_bytes
        if free_bytes < required:
            raise DomainError(
                "QUICK_DISK_CAPACITY_EXCEEDED",
                "快速分析工作盘空间不足，请等待任务清理或联系管理员扩容",
                507,
                [
                    {
                        "free_bytes": free_bytes,
                        "required_bytes": required,
                        "reservation_bytes": reservation_bytes,
                    }
                ],
            )

    def ensure_quota(
        self,
        *,
        global_used_bytes: int,
        user_used_bytes: int,
        reservation_bytes: int,
    ) -> None:
        if global_used_bytes + reservation_bytes > self.global_capacity_bytes:
            raise DomainError(
                "QUICK_GLOBAL_CAPACITY_EXCEEDED",
                "快速分析全局容量已满，请等待过期结果清理",
                409,
            )
        if user_used_bytes + reservation_bytes > self.user_capacity_bytes:
            raise DomainError(
                "QUICK_USER_CAPACITY_EXCEEDED",
                "当前用户的快速分析容量已满，请等待已有任务完成或结果清理",
                409,
            )
