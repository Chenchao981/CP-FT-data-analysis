from __future__ import annotations

import json
from typing import Any

import pytest
from app.infrastructure.existing_cleaner_runner import CleanerArtifact
from app.infrastructure.initial_import_staging import (
    FINALIZE_FINGERPRINT_MISMATCH,
    FINALIZE_LEASE_EXPIRED,
    FINALIZE_LEASE_MISMATCH,
    AtomicStagePreparation,
    InitialImportStageError,
    insert_draft_dataset_version,
    prepare_atomic_stage,
    record_atomic_stage,
)

LEASE = "11111111-1111-1111-1111-111111111111"


class _Result:
    def __init__(
        self,
        *,
        one: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
        scalar: int | None = None,
        rowcount: int = 1,
    ) -> None:
        self._one = one
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._one

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def scalar_one(self) -> int:
        assert self._scalar is not None
        return self._scalar


class _Connection:
    def __init__(self, *results: _Result) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, object]] = []

    def execute(self, statement: object, parameters: object = None) -> _Result:
        self.calls.append((str(statement), parameters))
        if self._results:
            return self._results.pop(0)
        return _Result()


def _context(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "import_batch_id": 17,
        "owner_user_id": 8,
        "business_domain": "PRODUCTION",
        "test_stage": "FT",
        "factory_code": "RIYUEXIN",
        "batch_status": "PROCESSING",
        "job_import_batch_id": 17,
        "job_status": "RUNNING",
        "cleaner_release_id": 5,
        "finalize_protocol": "ATOMIC_V1",
        "lease_token": LEASE,
        "lease_expires_at_utc": "2099-01-01T00:00:00Z",
        "lease_is_live": 1,
        "attempt_count": 2,
        "output_contract_version": "FT_XLSX_SCATTER_V1",
    }
    base.update(overrides)
    return base


def _inputs() -> list[dict[str, object]]:
    return [
        {
            "import_batch_file_id": 101,
            "ordinal_no": 1,
            "file_role": "DETAIL",
            "receipt_id": 201,
            "source_file_id": 301,
            "sha256": "a" * 64,
        },
        {
            "import_batch_file_id": 102,
            "ordinal_no": 2,
            "file_role": "SPEC",
            "receipt_id": 202,
            "source_file_id": 302,
            "sha256": "b" * 64,
        },
    ]


def _artifacts(path: str = "C:/private/cleaned.xlsx") -> tuple[CleanerArtifact, ...]:
    return (CleanerArtifact("cleaned", path, 123, "c" * 64),)


def _prepare(
    *,
    context: dict[str, object] | None = None,
    existing: dict[str, object] | None = None,
    artifacts: tuple[CleanerArtifact, ...] | None = None,
) -> tuple[AtomicStagePreparation, _Connection]:
    connection = _Connection(
        _Result(one=context or _context()),
        _Result(rows=_inputs()),
        _Result(one=existing),
    )
    preparation = prepare_atomic_stage(
        connection,  # type: ignore[arg-type]
        job_id=41,
        import_batch_id=17,
        lease_token=LEASE,
        artifacts=artifacts or _artifacts(),
    )
    return preparation, connection


def test_atomic_stage_validates_live_lease_without_blocking_heartbeat_and_hashes_manifest() -> None:
    preparation, connection = _prepare()
    other_path, _ = _prepare(artifacts=_artifacts("D:/other/location/cleaned.xlsx"))

    assert "WITH (UPDLOCK,HOLDLOCK)" not in connection.calls[0][0]
    assert "finalize_protocol" in connection.calls[0][0]
    assert preparation.source_file_id == 301
    assert preparation.import_batch_file_ids == (101, 102)
    assert preparation.input_manifest_sha256 == other_path.input_manifest_sha256
    manifest = json.loads(preparation.input_manifest_json)
    assert [item["source_file_id"] for item in manifest["batch_files"]] == [301, 302]
    assert manifest["cleaner_artifacts"] == [
        {"role": "cleaned", "size_bytes": 123, "sha256": "c" * 64}
    ]
    assert "private" not in preparation.input_manifest_json
    assert "location" not in preparation.input_manifest_json


def test_atomic_stage_rejects_wrong_lease_before_reading_inputs() -> None:
    connection = _Connection(_Result(one=_context(lease_token="2" * 32)))

    with pytest.raises(InitialImportStageError) as error:
        prepare_atomic_stage(
            connection,  # type: ignore[arg-type]
            job_id=41,
            import_batch_id=17,
            lease_token=LEASE,
            artifacts=_artifacts(),
        )

    assert error.value.error_code == FINALIZE_LEASE_MISMATCH
    assert len(connection.calls) == 1


def test_atomic_stage_rejects_expired_matching_lease_before_reading_inputs() -> None:
    connection = _Connection(
        _Result(
            one=_context(
                lease_expires_at_utc="2020-01-01T00:00:00Z",
                lease_is_live=0,
            )
        )
    )

    with pytest.raises(InitialImportStageError) as error:
        prepare_atomic_stage(
            connection,  # type: ignore[arg-type]
            job_id=41,
            import_batch_id=17,
            lease_token=LEASE,
            artifacts=_artifacts(),
        )

    assert error.value.error_code == FINALIZE_LEASE_EXPIRED
    assert "lease_expires_at_utc>SYSUTCDATETIME()" in connection.calls[0][0]
    assert len(connection.calls) == 1


def test_atomic_stage_reuses_matching_staged_fingerprint_and_rejects_mismatch() -> None:
    first, _ = _prepare()
    existing = {
        "status": "STAGED",
        "input_manifest_sha256": first.input_manifest_sha256,
        "processing_run_id": 501,
        "dataset_version_id": 601,
        "dataset_id": 701,
        "version_no": 3,
        "spec_set_id": None,
        "unit_count": 11,
        "measurement_count": 22,
        "version_status": "DRAFT",
        "is_current": False,
        "run_status": "READY",
    }
    reused, connection = _prepare(existing=existing)

    assert reused.existing is not None
    assert reused.existing.processing_run_id == 501
    assert reused.existing.dataset_version_id == 601
    assert "staged_attempt_count" in connection.calls[-1][0]
    assert connection.calls[-1][1] == {"job": 41, "attempt": 2}

    with pytest.raises(InitialImportStageError) as error:
        _prepare(existing=replace_manifest_hash(existing, "d" * 64))
    assert error.value.error_code == FINALIZE_FINGERPRINT_MISMATCH


def replace_manifest_hash(
    existing: dict[str, object], fingerprint: str
) -> dict[str, object]:
    return {**existing, "input_manifest_sha256": fingerprint}


def test_dataset_stage_is_draft_and_records_every_input_lineage_plus_intent() -> None:
    preparation, _ = _prepare()
    connection = _Connection(_Result(scalar=4), _Result(scalar=604))

    version_id, version_no = insert_draft_dataset_version(
        connection,  # type: ignore[arg-type]
        dataset_id=77,
        import_batch_id=17,
        unit_count=11,
        measurement_count=22,
        spec_set_id=None,
        metadata_json="{}",
    )
    record_atomic_stage(
        connection,  # type: ignore[arg-type]
        job_id=41,
        import_batch_id=17,
        processing_run_id=501,
        dataset_version_id=version_id,
        preparation=preparation,
    )

    assert (version_id, version_no) == (604, 4)
    version_insert = connection.calls[1][0]
    assert "'DRAFT',0" in version_insert
    assert "NULL,NULL,NULL" in version_insert
    assert "'PUBLISHED'" not in version_insert
    assert all("SUPERSEDED" not in sql for sql, _ in connection.calls)
    fence_sql = next(sql for sql, _ in connection.calls if "UPDATE j SET heartbeat_at_utc" in sql)
    assert "WITH (UPDLOCK,HOLDLOCK)" in fence_sql
    assert "lease_expires_at_utc>SYSUTCDATETIME()" in fence_sql
    lineage_sql, lineage_parameters = next(
        (sql, parameters)
        for sql, parameters in connection.calls
        if "processing_run_input_file" in sql
    )
    assert "processing_run_input_file" in lineage_sql
    assert "'WRITER_VERIFIED'" in lineage_sql
    assert lineage_parameters == [
        {"processing": 501, "batch_file": 101},
        {"processing": 501, "batch_file": 102},
    ]
    intent_sql, intent_parameters = connection.calls[-1]
    assert "initial_import_finalize_intent" in intent_sql
    assert "'STAGED'" in intent_sql
    assert intent_parameters == {
        "job": 41,
        "batch": 17,
        "processing": 501,
        "version": 604,
        "fingerprint": preparation.input_manifest_sha256,
        "manifest": preparation.input_manifest_json,
        "attempt": 2,
    }


def test_atomic_stage_rolls_back_when_commit_fence_loses_the_lease() -> None:
    preparation, _ = _prepare()
    connection = _Connection(_Result(rowcount=0))

    with pytest.raises(InitialImportStageError) as error:
        record_atomic_stage(
            connection,  # type: ignore[arg-type]
            job_id=41,
            import_batch_id=17,
            processing_run_id=501,
            dataset_version_id=604,
            preparation=preparation,
        )

    assert error.value.error_code == FINALIZE_LEASE_EXPIRED
    assert len(connection.calls) == 1
