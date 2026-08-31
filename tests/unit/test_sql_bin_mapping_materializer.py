from __future__ import annotations

from typing import Any

import pytest
from app.infrastructure.sql_bin_mapping_materializer import (
    BinMappingMaterializationError,
    materialize_processing_run_bin_mappings,
)


class _CaptureConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: Any, parameters: dict[str, Any]) -> None:
        self.calls.append((str(statement), parameters))


def _normalized(sql: str) -> str:
    return " ".join(sql.split())


def test_resolver_is_scope_priority_effective_time_and_definition_strict() -> None:
    connection = _CaptureConnection()

    materialize_processing_run_bin_mappings(  # type: ignore[arg-type]
        connection,
        processing_run_id=71,
    )

    assert len(connection.calls) == 1
    sql, parameters = connection.calls[0]
    compact = _normalized(sql)
    assert parameters == {
        "processing_run_id": 71,
        "lock_resource": "TMS_BIN_MAPPING_PROCESSING_RUN:71",
    }
    assert "bms.active=1" in compact
    assert "sp.active=1" in compact
    assert "bms.supplier_id IS NULL OR bms.supplier_id=rc.supplier_id" in compact
    assert "bms.product_id IS NULL OR bms.product_id=rc.product_id" in compact
    assert "bms.test_stage IS NULL OR bms.test_stage=rc.test_stage" in compact
    assert (
        "bms.program_version_id IS NULL OR bms.program_version_id=rc.program_version_id"
    ) in compact
    assert "bms.effective_from_utc<=rc.event_time_utc" in compact
    assert "bms.effective_to_utc>rc.event_time_utc" in compact
    assert "ORDER BY sp.priority DESC" in compact
    assert "WHERE priority_rank=1" in compact
    assert "bd.bin_type=ub.bin_type" in compact
    assert "bd.bin_code=ub.raw_bin_code" in compact


def test_materializer_classifies_and_snapshots_without_guessing_master_data() -> None:
    connection = _CaptureConnection()

    materialize_processing_run_bin_mappings(  # type: ignore[arg-type]
        connection,
        processing_run_id=72,
    )

    compact = _normalized(connection.calls[0][0])
    for status in ("MATCHED", "NO_MATCH", "CONFIG_AMBIGUOUS", "INVALID"):
        assert f"'{status}'" in compact
    assert (
        "CASE WHEN mapping_status='MATCHED' THEN unique_mapping_set_id END" in compact
    )
    assert "CASE WHEN mapping_status='MATCHED' THEN bin_definition_id END" in compact
    assert (
        "CASE WHEN mapping_status='MATCHED' THEN CAST(is_pass_value AS bit) END"
        in compact
    )
    assert "CASE WHEN mapping_status='MATCHED' THEN failure_mode END" in compact
    assert "INSERT mdm.bin_mapping_set" not in compact
    assert "INSERT mdm.bin_definition" not in compact
    assert "INSERT mdm.scope_priority" not in compact


def test_materializer_is_serialized_and_idempotent_per_unit_bin_type() -> None:
    connection = _CaptureConnection()

    materialize_processing_run_bin_mappings(  # type: ignore[arg-type]
        connection,
        processing_run_id=73,
    )

    compact = _normalized(connection.calls[0][0])
    assert "sys.sp_getapplock" in compact
    assert "@LockOwner='Transaction'" in compact
    assert "PRIMARY KEY CLUSTERED(unit_id, bin_type)" in compact
    assert "HAVING COUNT_BIG(*)>1" in compact
    assert "BIN_MAPPING_DUPLICATE_UNIT_TYPE" in compact
    assert "UPDATE target WITH (UPDLOCK, HOLDLOCK)" in compact
    assert "WHERE NOT EXISTS" in compact
    assert "existing.unit_id=stage.unit_id" in compact
    assert "existing.bin_type=stage.bin_type" in compact


@pytest.mark.parametrize("processing_run_id", [0, -1, True])
def test_materializer_rejects_invalid_processing_run_id(
    processing_run_id: int,
) -> None:
    connection = _CaptureConnection()

    with pytest.raises(
        BinMappingMaterializationError,
        match="processing_run_id must be positive",
    ):
        materialize_processing_run_bin_mappings(  # type: ignore[arg-type]
            connection,
            processing_run_id=processing_run_id,
        )

    assert connection.calls == []
