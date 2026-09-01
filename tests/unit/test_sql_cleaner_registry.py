from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from app.core.errors import DomainError
from app.infrastructure.sql_cleaner_registry import SqlCleanerRegistry


def _release_row() -> dict[str, object]:
    return {
        "cleaner_release_id": 21,
        "format_profile_id": 8,
        "test_stage": "FT",
        "factory_code": "JIEQUN",
        "format_code": "JIEQUN_FT_QUICK_PAT_EXISTING",
        "profile_version": "route-a-v1",
        "cleaner_code": "JIEQUN_FT_QUICK_PAT_EXISTING",
        "cleaner_version": "v2.15.0",
        "code_checksum": "a" * 64,
        "artifact_uri": "cleaner.pyz",
        "runtime_uri": "python.exe",
        "entrypoint": "generate_raw_pat",
        "adapter_code": "JIEQUN_FT_QUICK_PAT_PYZ",
        "input_contract_version": "JIEQUN_UNIFIED_CSV_DIRECTORY_V1",
        "output_contract_version": "FT_PAT_RESULT_V1",
        "execution_config_json": None,
        "timeout_seconds": 7200,
        "max_output_bytes": 64 * 1024 * 1024,
    }


def _registry_with_row(row: dict[str, object] | None):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.one_or_none.return_value = row
    return SqlCleanerRegistry(engine), connection


def _lookup(registry: SqlCleanerRegistry):
    return registry.latest_released_for_contract(
        test_stage=" ft ",
        factory_code=" jiequn ",
        format_code=" jiequn_ft_quick_pat_existing ",
        cleaner_code=" jiequn_ft_quick_pat_existing ",
        adapter_code=" jiequn_ft_quick_pat_pyz ",
        input_contract_version=" jiequn_unified_csv_directory_v1 ",
        output_contract_version=" ft_pat_result_v1 ",
    )


def test_exact_released_lookup_filters_every_approved_contract_field() -> None:
    registry, connection = _registry_with_row(_release_row())

    release = _lookup(registry)

    statement, values = connection.execute.call_args.args
    sql = str(statement)
    assert release.cleaner_release_id == 21
    for predicate in (
        "fp.test_stage=:stage",
        "fp.factory_code=:factory",
        "fp.format_code=:format_code",
        "cr.cleaner_code=:cleaner_code",
        "cr.adapter_code=:adapter_code",
        "cr.input_contract_version=:input_contract",
        "cr.output_contract_version=:output_contract",
    ):
        assert predicate in sql
    assert values == {
        "stage": "FT",
        "factory": "JIEQUN",
        "format_code": "JIEQUN_FT_QUICK_PAT_EXISTING",
        "cleaner_code": "JIEQUN_FT_QUICK_PAT_EXISTING",
        "adapter_code": "JIEQUN_FT_QUICK_PAT_PYZ",
        "input_contract": "JIEQUN_UNIFIED_CSV_DIRECTORY_V1",
        "output_contract": "FT_PAT_RESULT_V1",
    }


def test_exact_released_lookup_fails_closed_when_no_contract_matches() -> None:
    registry, _connection = _registry_with_row(None)

    with pytest.raises(DomainError) as captured:
        _lookup(registry)

    assert captured.value.code == "CLEANER_RELEASE_NOT_AVAILABLE"
    assert captured.value.status_code == 409
