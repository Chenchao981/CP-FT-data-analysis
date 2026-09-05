from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from app.cleaners.huahong_dcp import HuaHongDcpParser
from app.infrastructure.canonical_writer import (
    CanonicalWriteError,
    HuaHongCanonicalWriter,
    HuaHongWriteContext,
    SourceFileRepository,
    SourceRegistration,
)

from tests.unit.test_huahong_dcp import source_text


class FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def one_or_none(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class FakeResult:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None, scalar: int | None = None) -> None:
        self.rows = rows or []
        self.scalar = scalar

    def mappings(self) -> FakeMappings:
        return FakeMappings(self.rows)

    def scalar_one(self) -> int:
        assert self.scalar is not None
        return self.scalar


class WriterConnection:
    def __init__(
        self,
        file: Any,
        item_ids: dict[str, int],
        *,
        job_status: str = "RUNNING",
        parser_version: str = "1.0",
    ) -> None:
        self.file = file
        self.item_ids = item_ids
        self.job_status = job_status
        self.parser_version = parser_version
        self.executions: list[tuple[str, Any]] = []
        self.next_unit_id = 100

    def in_transaction(self) -> bool:
        return True

    def execute(self, statement: Any, parameters: Any = None) -> FakeResult:
        sql = str(statement)
        self.executions.append((sql, parameters))
        if "SELECT sf.sha256" in sql:
            return FakeResult(
                rows=[
                    {
                        "sha256": self.file.source_sha256,
                        "job_source_file_id": 10,
                        "supplier_active": True,
                        "product_active": True,
                        "program_active": True,
                        "parser_active": True,
                        "job_status": self.job_status,
                        "format_code": "HUAHONG_DCP_TXT",
                        "parser_version": self.parser_version,
                        "program_version_id": 50,
                    }
                ]
            )
        if "FROM mdm.test_item_definition" in sql:
            return FakeResult(
                rows=[
                    {"raw_item_name": name, "test_item_id": identity}
                    for name, identity in self.item_ids.items()
                ]
            )
        if "INSERT ingestion.processing_run" in sql:
            return FakeResult(scalar=70)
        if "INSERT test.test_run" in sql:
            return FakeResult(scalar=80)
        if "sp_sequence_get_range" in sql:
            first = self.next_unit_id + 1
            self.next_unit_id += parameters["count"]
            return FakeResult(scalar=first)
        return FakeResult()


class FakeEngine:
    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.begin_count = 0

    @contextmanager
    def begin(self):
        self.begin_count += 1
        yield self.connection


def _parsed_file():
    return HuaHongDcpParser().parse_text(
        source_text(), source_name="FA00-0001-000A-260820@203_001.TXT"
    )


def _context(item_ids: dict[str, int]) -> HuaHongWriteContext:
    return HuaHongWriteContext(
        source_file_id=10,
        job_id=20,
        parser_profile_id=30,
        supplier_id=40,
        product_id=45,
        program_version_id=50,
        test_item_ids=item_ids,
    )


def _cp_context_without_product(item_ids: dict[str, int]) -> HuaHongWriteContext:
    return HuaHongWriteContext(
        source_file_id=10,
        job_id=20,
        parser_profile_id=30,
        supplier_id=40,
        product_id=None,
        program_version_id=50,
        test_item_ids=item_ids,
    )


def test_writer_rejects_incomplete_test_item_mapping_before_transaction() -> None:
    file = _parsed_file()
    connection = WriterConnection(file, {})
    engine = FakeEngine(connection)

    with pytest.raises(CanonicalWriteError, match="exactly match"):
        HuaHongCanonicalWriter(engine).write(file, _context({}))  # type: ignore[arg-type]

    assert engine.begin_count == 0


def test_writer_persists_traceable_canonical_rows_in_one_transaction() -> None:
    file = _parsed_file()
    item_ids = {name: index for index, name in enumerate(file.parameters, start=1000)}
    connection = WriterConnection(file, item_ids)
    engine = FakeEngine(connection)

    result = HuaHongCanonicalWriter(engine, measurement_batch_size=3).write(
        file, _context(item_ids)  # type: ignore[arg-type]
    )

    assert engine.begin_count == 1
    assert result.processing_run_id == 70
    assert result.test_run_id == 80
    assert result.unit_count == 1
    assert result.measurement_count == len(file.parameters)
    unit_parameters = next(
        params[0] for sql, params in connection.executions if "INSERT test.cp_die(" in sql
    )
    assert unit_parameters["source_row_no"] == 16
    assert file.source_sha256 in unit_parameters["metadata_json"]
    measurement_batches = [
        params
        for sql, params in connection.executions
        if "INSERT test.cp_measurement(" in sql
    ]
    assert sum(len(batch) for batch in measurement_batches) == len(file.parameters)
    assert measurement_batches[0][0]["source_column_index"] == 5
    assert measurement_batches[0][0]["raw_value"] == "2E-8"
    mapping_calls = [
        params
        for sql, params in connection.executions
        if "INSERT test.unit_bin_evaluation" in sql
    ]
    assert mapping_calls == [
        {
            "processing_run_id": 70,
            "lock_resource": "TMS_BIN_MAPPING_PROCESSING_RUN:70",
        }
    ]
    spec_evaluation_calls = [
        params
        for sql, params in connection.executions
        if "INSERT test.measurement_evaluation" in sql
    ]
    assert spec_evaluation_calls == [
        {
            "processing_run_id": 70,
            "lock_resource": "TMS_SPEC_EVALUATION_PROCESSING_RUN:70",
        }
    ]


def test_cp_writer_accepts_missing_optional_product() -> None:
    file = _parsed_file()
    item_ids = {name: index for index, name in enumerate(file.parameters, start=1000)}
    connection = WriterConnection(file, item_ids)
    engine = FakeEngine(connection)

    HuaHongCanonicalWriter(engine).write(  # type: ignore[arg-type]
        file, _cp_context_without_product(item_ids)
    )

    run_parameters = next(
        params for sql, params in connection.executions if "INSERT test.test_run" in sql
    )
    assert run_parameters["product_id"] is None


def test_writer_rejects_job_that_is_not_running() -> None:
    file = _parsed_file()
    item_ids = {name: index for index, name in enumerate(file.parameters, start=1000)}
    engine = FakeEngine(WriterConnection(file, item_ids, job_status="QUEUED"))

    with pytest.raises(CanonicalWriteError, match="must be RUNNING"):
        HuaHongCanonicalWriter(engine).write(file, _context(item_ids))  # type: ignore[arg-type]


class SourceConnection:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, statement: Any, parameters: Any = None) -> FakeResult:
        sql = str(statement)
        if "SELECT source_file_id" in sql:
            self.calls += 1
            return FakeResult(rows=[])
        if "INSERT ingestion.source_file(" in sql:
            return FakeResult(scalar=7)
        if "INSERT ingestion.source_file_receipt" in sql:
            return FakeResult(scalar=8)
        raise AssertionError(sql)


def test_source_registration_creates_content_identity_and_receipt() -> None:
    engine = FakeEngine(SourceConnection())
    receipt = SourceFileRepository(engine).register(  # type: ignore[arg-type]
        SourceRegistration(
            sha256="a" * 64,
            file_size=123,
            canonical_storage_uri="nas://tms/raw/a.txt",
            original_file_name="a.txt",
        )
    )
    assert receipt.source_file_id == 7
    assert receipt.receipt_id == 8
    assert receipt.is_duplicate_receipt is False


def test_source_registration_rejects_invalid_sha_before_transaction() -> None:
    engine = FakeEngine(SourceConnection())
    with pytest.raises(CanonicalWriteError, match="SHA256"):
        SourceFileRepository(engine).register(  # type: ignore[arg-type]
            SourceRegistration(
                sha256="bad",
                file_size=123,
                canonical_storage_uri="nas://tms/raw/a.txt",
                original_file_name="a.txt",
            )
        )
    assert engine.begin_count == 0
