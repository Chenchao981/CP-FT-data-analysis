from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Engine, text

from app.core.errors import DomainError
from app.domain.cleaner_registry import CleanerRelease

RELEASE_COLUMNS = """
cr.cleaner_release_id,cr.format_profile_id,fp.test_stage,fp.factory_code,
fp.format_code,fp.profile_version,cr.cleaner_code,cr.cleaner_version,
cr.code_checksum,cr.artifact_uri,cr.runtime_uri,cr.entrypoint,cr.adapter_code,
cr.input_contract_version,cr.output_contract_version,cr.execution_config_json,
cr.timeout_seconds,cr.max_output_bytes
"""


def _to_release(row: Mapping[str, Any]) -> CleanerRelease:
    required = (
        "factory_code",
        "artifact_uri",
        "runtime_uri",
        "entrypoint",
        "adapter_code",
        "input_contract_version",
        "output_contract_version",
    )
    missing = [name for name in required if not row[name]]
    if missing:
        raise DomainError(
            "CLEANER_RELEASE_INCOMPLETE",
            "已发布 Cleaner 缺少可执行合同字段：" + ", ".join(missing),
            409,
        )
    return CleanerRelease(
        cleaner_release_id=int(row["cleaner_release_id"]),
        format_profile_id=int(row["format_profile_id"]),
        test_stage=str(row["test_stage"]),
        factory_code=str(row["factory_code"]),
        format_code=str(row["format_code"]),
        profile_version=str(row["profile_version"]),
        cleaner_code=str(row["cleaner_code"]),
        cleaner_version=str(row["cleaner_version"]),
        code_checksum=str(row["code_checksum"]),
        artifact_uri=str(row["artifact_uri"]),
        runtime_uri=str(row["runtime_uri"]),
        entrypoint=str(row["entrypoint"]),
        adapter_code=str(row["adapter_code"]),
        input_contract_version=str(row["input_contract_version"]),
        output_contract_version=str(row["output_contract_version"]),
        execution_config_json=row["execution_config_json"],
        timeout_seconds=int(row["timeout_seconds"]),
        max_output_bytes=int(row["max_output_bytes"]),
    )


class SqlCleanerRegistry:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_released(self, cleaner_release_id: int) -> CleanerRelease:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        f"SELECT {RELEASE_COLUMNS} "
                        "FROM ingestion.cleaner_release cr "
                        "JOIN ingestion.format_profile fp "
                        "ON fp.format_profile_id=cr.format_profile_id "
                        "WHERE cr.cleaner_release_id=:release_id "
                        "AND cr.status='RELEASED' AND fp.status='RELEASED'"
                    ),
                    {"release_id": cleaner_release_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise DomainError(
                "CLEANER_RELEASE_NOT_AVAILABLE",
                f"Cleaner Release {cleaner_release_id} 不存在或尚未发布",
                404,
            )
        return _to_release(row)

    def latest_released(self, test_stage: str, factory_code: str) -> CleanerRelease:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        f"SELECT TOP (1) {RELEASE_COLUMNS} "
                        "FROM ingestion.cleaner_release cr "
                        "JOIN ingestion.format_profile fp "
                        "ON fp.format_profile_id=cr.format_profile_id "
                        "WHERE cr.status='RELEASED' AND fp.status='RELEASED' "
                        "AND fp.test_stage=:stage AND fp.factory_code=:factory "
                        "ORDER BY cr.approved_at_utc DESC,cr.created_at_utc DESC,"
                        "cr.cleaner_release_id DESC"
                    ),
                    {
                        "stage": test_stage.strip().upper(),
                        "factory": factory_code.strip().upper(),
                    },
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise DomainError(
                "CLEANER_RELEASE_NOT_AVAILABLE",
                f"{test_stage.upper()}/{factory_code.upper()} 没有已发布 Cleaner",
                409,
            )
        return _to_release(row)
