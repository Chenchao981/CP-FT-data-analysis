from __future__ import annotations

from pathlib import Path

import pytest
from app.domain.cleaner_registry import CleanerRelease
from app.domain.jobs import InMemoryJobService
from app.domain.stage_data import (
    BatchFileInfo,
    BatchInfo,
    StageResultRow,
    StageUploadRow,
)
from app.infrastructure.existing_cleaner_runner import (
    CleanerArtifact,
    ExistingCleanerRunResult,
)
from app.infrastructure.source_catalog import SourceCatalog, SourceRoot
from app.main import create_app
from fastapi.testclient import TestClient


class StubStageService:
    def __init__(self) -> None:
        self.principal = None
        self.calls: list[tuple[str, str]] = []
        self.archived: list[int] = []
        self.stored_path = Path("unused")
        self.queued: list[int] = []
        self.registered_files = ()

    def register_upload(
        self, principal, business_domain, test_stage, factory_code, files, remark
    ):
        self.principal = principal
        self.registered_files = files
        self.calls.append((business_domain, test_stage))
        assert business_domain in {"ENGINEERING", "PRODUCTION"}
        assert test_stage in {"CP", "FT"}
        assert factory_code in {
            "huahong",
            "jetech",
            "lion",
            "riyuexin",
            "riyueguang",
        }
        assert files[0].sha256
        return 41

    def mark_processing(self, batch_id, principal):
        assert batch_id == 41
        assert principal.user_id == 1
        return 73

    def mark_queued(self, batch_id):
        self.queued.append(batch_id)

    def record_result(self, batch_id, job_id, result):
        if result["data_type"] == "CP":
            assert result["lot_id"] == "FA5X-2565"
            assert result["product_name"] == "NCETEN30CAC"
        else:
            assert result["lot_id"] == "FA59-3997"
            assert result["product_name"] == "NCEAP40PT15D(M)-2B00"

    def mark_failed(self, batch_id, job_id, message):
        raise AssertionError(message)

    def list_uploads(self, principal, business_domain, test_stage):
        self.calls.append((business_domain, test_stage))
        return (
            StageUploadRow(
                41,
                1,
                9001,
                "sample.zip",
                "zip",
                100,
                "huahong",
                "2026-08-21T00:00:00",
                None,
                principal.login_name,
                principal.display_name,
                "NEEDS_INPUT",
                7001,
                73,
                "LOT_ID_REQUIRED",
                "请确认批次号",
                "LOT_ID",
            ),
        )

    def list_results(self, principal, business_domain, test_stage):
        self.calls.append((business_domain, test_stage))
        return (
            StageResultRow(
                1,
                41,
                "FA5X-2565",
                "NCETEN30CAC",
                "FA5X-2565",
                1,
                "huahong",
                2,
                10,
                9,
                0.9,
                "PROCESSED",
                "CP",
                31,
                1,
                "2026-08-21T00:00:00",
            ),
        )

    def get_batch_info(self, principal, business_domain, test_stage, batch_id):
        assert batch_id == 41
        return BatchInfo(
            41,
            "huahong",
            "PROCESSED",
            (
                BatchFileInfo(
                    9001,
                    "sample.zip",
                    str(self.stored_path),
                    7001,
                    "0" * 64,
                ),
            ),
        )

    def archive_previous_results(self, batch_id):
        self.archived.append(batch_id)


def _stub_cp_cleaner(monkeypatch, tmp_path: Path) -> None:
    cleaned = tmp_path / "FA5X-2565_cleaned_20260821_000000.csv"
    cleaned.write_text(
        "Lot_ID,Wafer_ID,Seq,Bin,X,Y,VTH,RDSON\nFA5X-2565,1,1,1,1,1,4,0.1\n",
        encoding="utf-8",
    )
    yield_file = tmp_path / "FA5X-2565_yield_20260821_000000.csv"
    yield_file.write_text(
        "Product_Name,Lot_ID,Wafer_ID,Yield,Total,Pass\nNCETEN30CAC,FA5X-2565,1,90%,10,9\nNCETEN30CAC,ALL,ALL,90%,10,9\n",
        encoding="utf-8",
    )
    result = ExistingCleanerRunResult(
        "CP",
        "huahong",
        str(tmp_path),
        (
            CleanerArtifact("cleaned", str(cleaned), cleaned.stat().st_size, "0" * 64),
            CleanerArtifact(
                "yield", str(yield_file), yield_file.stat().st_size, "1" * 64
            ),
        ),
        "ok",
    )

    class StubRunner:
        def run(self, **kwargs):
            assert kwargs["test_stage"] == "CP"
            assert kwargs["factory"] == "huahong"
            return result

    monkeypatch.setenv("TMS_UPLOAD_ROOT", str(tmp_path / "raw"))
    monkeypatch.setattr(
        "app.api.stage_data.ExistingCleanerRunner", StubRunner, raising=False
    )


def _stub_ft_cleaner(monkeypatch, tmp_path: Path) -> None:
    manifest = tmp_path / "ft_scatter_manifest.json"
    manifest.write_text(
        '{"schema_version":1,"factory":"日月新（ASE）","data_type":"DC","row_count":100,"parameters":["VTH1(V)","BVDSS1(V)"],"sources":["NCT5542087"],"lots":["FA59-3997"]}',
        encoding="utf-8",
    )
    spec = tmp_path / "ft_scatter_spec.csv"
    spec.write_text(
        "Source_ID,lot_ID,Parameter,Unit,Low_Limit,High_Limit,Source_File\nNCT5542087,FA59-3997,VTH1(V),V,1.3,2.2,NCT5542087_NCEAP40PT15D(M)-2B00_FA59-3997_20251024_182422.xlsx\n",
        encoding="utf-8-sig",
    )
    cleaned = tmp_path / "FA59-3997_001.xlsx"
    cleaned.write_text("workbook", encoding="utf-8")
    seen: dict = {}

    class StubRunner:
        def run(self, **kwargs):
            seen.update(kwargs)
            assert kwargs["test_stage"] == "FT"
            assert len(kwargs["inputs"]) == 1 and Path(kwargs["inputs"][0]).is_dir()
            return ExistingCleanerRunResult(
                "FT",
                "riyuexin",
                str(tmp_path),
                (
                    CleanerArtifact(
                        "cleaned", str(cleaned), cleaned.stat().st_size, "0" * 64
                    ),
                    CleanerArtifact(
                        "scatter_manifest",
                        str(manifest),
                        manifest.stat().st_size,
                        "1" * 64,
                    ),
                    CleanerArtifact(
                        "scatter_spec", str(spec), spec.stat().st_size, "2" * 64
                    ),
                ),
                "ok",
            )

    monkeypatch.setenv("TMS_UPLOAD_ROOT", str(tmp_path / "raw"))
    monkeypatch.setattr(
        "app.api.stage_data.ExistingCleanerRunner", StubRunner, raising=False
    )
    return seen


def _client(
    service: StubStageService, catalog: SourceCatalog | None = None
) -> TestClient:
    app = create_app()
    app.state.stage_data_service = service
    app.state.cleaner_registry = StubCleanerRegistry()
    if catalog is not None:
        app.state.source_catalog = catalog
    return TestClient(app)


class StubCleanerRegistry:
    def latest_released(self, test_stage, factory_code):
        return CleanerRelease(
            cleaner_release_id=17 if test_stage == "CP" else 18,
            format_profile_id=7,
            test_stage=test_stage,
            factory_code=factory_code,
            format_code="TEST",
            profile_version="v1",
            cleaner_code=f"{factory_code}_{test_stage}",
            cleaner_version="v1",
            code_checksum="0" * 64,
            artifact_uri="unused.pyz",
            runtime_uri="python.exe",
            entrypoint="test",
            adapter_code="test",
            input_contract_version="v1",
            output_contract_version="v1",
            execution_config_json=None,
            timeout_seconds=60,
            max_output_bytes=1000,
        )


class AtomicInitialImportQueue:
    def __init__(self) -> None:
        self._jobs = InMemoryJobService()
        self.allowed_statuses: list[tuple[str, ...]] = []

    def create_initial_import_for_batch(
        self, payload, principal, *, allowed_batch_statuses
    ):
        assert payload.requested_by_user_id == principal.user_id
        self.allowed_statuses.append(allowed_batch_statuses)
        return self._jobs.create(payload)


def test_engineering_cp_upload_queues_authenticated_route_a_job(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    service = StubStageService()
    response = _client(service).post(
        "/api/v1/engineering/cp/uploads",
        files={"files": ("sample.zip", b"sample", "application/zip")},
        data={"factory_code": "huahong"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["business_domain"] == "ENGINEERING"
    assert body["test_stage"] == "CP"
    assert body["status"] == "QUEUED"
    assert body["cleaner_release"]["cleaner_release_id"] == 17
    assert body["uploader"]["user_id"] == 1
    assert service.principal.login_name == "development-admin"
    assert service.queued == [41]


def test_sql_upload_uses_atomic_batch_queue_and_job_creation(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    service = StubStageService()
    client = _client(service)
    queue = AtomicInitialImportQueue()
    client.app.state.job_service = queue

    response = client.post(
        "/api/v1/engineering/cp/uploads",
        files={"files": ("sample.zip", b"sample", "application/zip")},
        data={"factory_code": "huahong"},
    )

    assert response.status_code == 201
    assert queue.allowed_statuses == [("RECEIVED",)]
    assert service.queued == []


def test_production_ft_upload_queues_ft_release(monkeypatch, tmp_path: Path) -> None:
    _stub_ft_cleaner(monkeypatch, tmp_path)
    service = StubStageService()
    response = _client(service).post(
        "/api/v1/production/ft/uploads",
        files={
            "files": (
                "NCT5542087_NCEAP40PT15D(M)-2B00_FA59-3997_20251024_182422.xlsx",
                b"workbook",
                "application/octet-stream",
            )
        },
        data={"factory_code": "riyuexin"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["business_domain"] == "PRODUCTION"
    assert body["test_stage"] == "FT"
    assert body["status"] == "QUEUED"
    assert body["cleaner_release"]["cleaner_release_id"] == 18
    assert service.calls == [("PRODUCTION", "FT")]


def test_upload_rejects_factory_stage_mismatch(monkeypatch, tmp_path: Path) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    client = _client(StubStageService())
    mismatch = client.post(
        "/api/v1/production/ft/uploads",
        files={"files": ("a.xlsx", b"x", "application/octet-stream")},
        data={"factory_code": "huahong"},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "FACTORY_UNSUPPORTED"
    wrong_cp = client.post(
        "/api/v1/production/cp/uploads",
        files={"files": ("a.zip", b"x", "application/zip")},
        data={"factory_code": "riyuexin"},
    )
    assert wrong_cp.status_code == 422
    assert wrong_cp.json()["error"]["code"] == "FACTORY_UNSUPPORTED"


def test_upload_rejects_duplicate_basename_before_writing(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_ft_cleaner(monkeypatch, tmp_path)
    service = StubStageService()
    response = _client(service).post(
        "/api/v1/production/ft/uploads",
        files=[
            (
                "files",
                ("Sample.xlsx", b"first", "application/octet-stream"),
            ),
            (
                "files",
                ("sample.XLSX", b"second", "application/octet-stream"),
            ),
        ],
        data={"factory_code": "riyuexin"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DUPLICATE_UPLOAD_FILE_NAME"
    assert service.registered_files == ()
    assert not (tmp_path / "raw").exists()


def test_production_cp_upload_returns_queue_identity(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    service = StubStageService()
    response = _client(service).post(
        "/api/v1/production/cp/uploads",
        files={"files": ("sample.zip", b"sample", "application/zip")},
        data={"factory_code": "huahong"},
    )
    assert response.status_code == 201
    assert service.calls == [("PRODUCTION", "CP")]
    assert response.json()["job_id"] > 0


@pytest.mark.parametrize(
    ("factory", "filename"),
    [
        ("jetech", "sample.xls"),
        ("lion", "sample.xlsx"),
    ],
)
def test_cp_upload_accepts_registered_existing_company_cleaners(
    monkeypatch, tmp_path: Path, factory: str, filename: str
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    service = StubStageService()
    response = _client(service).post(
        "/api/v1/production/cp/uploads",
        files={"files": (filename, b"sample", "application/octet-stream")},
        data={"factory_code": factory},
    )

    assert response.status_code == 201
    assert response.json()["cleaner_release"]["cleaner_code"] == f"{factory.upper()}_CP"


def test_general_cp_upload_rejects_custom_guoyu_tool(monkeypatch, tmp_path: Path) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    response = _client(StubStageService()).post(
        "/api/v1/production/cp/uploads",
        files={"files": ("sample.zip", b"sample", "application/zip")},
        data={"factory_code": "guoyu"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FACTORY_UNSUPPORTED"


def test_cp_upload_snapshots_an_authorized_catalog_directory(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    source_root = tmp_path / "source"
    source = source_root / "C146808.02"
    source.mkdir(parents=True)
    first = source / "C146808-01.xls"
    second = source / "C146808-02.xls"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    catalog = SourceCatalog(
        (
            SourceRoot(
                "JETECH_ENGINEERING",
                "Jetech engineering source",
                source_root,
                "CP",
                "JETECH",
                (".xls",),
                "FORMAL_IMPORT",
                ("ENGINEERING",),
            ),
        )
    )
    service = StubStageService()
    response = _client(service, catalog).post(
        "/api/v1/engineering/cp/uploads",
        data={
            "factory_code": "jetech",
            "source_root_code": "JETECH_ENGINEERING",
            "source_relative_path": "C146808.02",
        },
    )

    assert response.status_code == 201
    assert response.json()["input_mode"] == "SOURCE_CATALOG"
    assert service.calls == [("ENGINEERING", "CP")]
    assert [item.original_name for item in service.registered_files] == [
        "C146808-01.xls",
        "C146808-02.xls",
    ]
    assert all(source not in item.path.parents for item in service.registered_files)
    assert all((tmp_path / "raw") in item.path.parents for item in service.registered_files)
    assert all(
        item.source_metadata["source_root_code"] == "JETECH_ENGINEERING"
        and item.source_metadata["snapshot_copy"] is True
        for item in service.registered_files
    )
    assert first.read_bytes() == b"one"
    assert second.read_bytes() == b"two"


def test_ft_catalog_snapshot_accepts_only_direct_dc_files(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    source_root = tmp_path / "source"
    source = source_root / "ft"
    source.mkdir(parents=True)
    (source / "sample.xlsx").write_bytes(b"sample")
    nested = source / "DVDS"
    nested.mkdir()
    (nested / "not-dc.xlsx").write_bytes(b"sample")
    catalog = SourceCatalog(
        (
            SourceRoot(
                "RIYUEXIN_PRODUCTION",
                "Riyuexin production source",
                source_root,
                "FT",
                "RIYUEXIN",
                (".xlsx",),
                "FORMAL_IMPORT",
                ("PRODUCTION",),
            ),
        )
    )
    service = StubStageService()
    response = _client(service, catalog).post(
        "/api/v1/production/ft/uploads",
        data={
            "factory_code": "riyuexin",
            "source_root_code": "RIYUEXIN_PRODUCTION",
            "source_relative_path": "ft",
        },
    )

    assert response.status_code == 201
    assert [item.original_name for item in service.registered_files] == [
        "sample.xlsx"
    ]


def test_formal_upload_rejects_legacy_absolute_source_path(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    response = _client(StubStageService()).post(
        "/api/v1/engineering/cp/uploads",
        data={"factory_code": "jetech", "source_path": str(tmp_path)},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SOURCE_PATH_UNSUPPORTED"


def test_formal_source_catalog_hides_physical_paths_and_enforces_scope(
    tmp_path: Path,
) -> None:
    catalog = SourceCatalog(
        (
            SourceRoot(
                "RIYUEXIN_PRODUCTION",
                "日月新量产 FT",
                tmp_path,
                "FT",
                "RIYUEXIN",
                (".xlsx",),
                "FORMAL_IMPORT",
                ("PRODUCTION",),
            ),
        )
    )
    client = _client(StubStageService(), catalog)

    roots = client.get(
        "/api/v1/production/ft/source-roots",
        params={"factory_code": "riyuexin"},
    )
    assert roots.status_code == 200
    assert roots.json()[0]["code"] == "RIYUEXIN_PRODUCTION"
    assert str(tmp_path) not in roots.text
    assert client.get(
        "/api/v1/engineering/ft/source-roots",
        params={"factory_code": "riyuexin"},
    ).json() == []
    forbidden = client.get(
        "/api/v1/engineering/ft/source-roots/RIYUEXIN_PRODUCTION/directories",
        params={"factory_code": "riyuexin", "relative_path": "."},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "SOURCE_ROOT_SCOPE_MISMATCH"


def test_formal_catalog_enforces_file_count_quota(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    monkeypatch.setenv("TMS_FORMAL_MAX_SOURCE_FILES", "1")
    source = tmp_path / "batch"
    source.mkdir()
    (source / "one.xls").write_bytes(b"one")
    (source / "two.xls").write_bytes(b"two")
    catalog = SourceCatalog(
        (
            SourceRoot(
                "JT_ROOT",
                "JT root",
                tmp_path,
                "CP",
                "JETECH",
                (".xls",),
                "FORMAL_IMPORT",
                ("ENGINEERING",),
            ),
        )
    )

    response = _client(StubStageService(), catalog).post(
        "/api/v1/engineering/cp/uploads",
        data={
            "factory_code": "jetech",
            "source_root_code": "JT_ROOT",
            "source_relative_path": "batch",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FORMAL_SOURCE_FILE_LIMIT_EXCEEDED"


def test_production_ft_upload_keeps_riyueguang_separate(monkeypatch, tmp_path: Path) -> None:
    _stub_ft_cleaner(monkeypatch, tmp_path)
    service = StubStageService()
    response = _client(service).post(
        "/api/v1/production/ft/uploads",
        files={
            "files": (
                "NCT6528068_NCEA75ED120BT(LA)-3B00_FA54-9744_20250722_070217.xlsx",
                b"workbook",
                "application/octet-stream",
            )
        },
        data={"factory_code": "riyueguang"},
    )

    assert response.status_code == 201
    assert response.json()["cleaner_release"]["cleaner_code"] == "RIYUEGUANG_FT"


def test_upload_rejects_unsupported_test_stage(monkeypatch, tmp_path: Path) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    response = _client(StubStageService()).post(
        "/api/v1/production/wat/uploads",
        files={"files": ("sample.zip", b"sample", "application/zip")},
        data={"factory_code": "huahong"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "STAGE_UPLOAD_UNSUPPORTED"


def test_upload_rejects_unknown_business_domain(monkeypatch, tmp_path: Path) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    response = _client(StubStageService()).post(
        "/api/v1/pilot/cp/uploads",
        files={"files": ("sample.zip", b"sample", "application/zip")},
        data={},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BUSINESS_DOMAIN_UNSUPPORTED"


def test_lists_are_scoped_by_business_domain_and_stage() -> None:
    service = StubStageService()
    client = _client(service)
    upload = client.get("/api/v1/engineering/cp/uploads").json()[0]
    assert upload["uploader_login"] == "development-admin"
    assert upload["source_file_id"] == 7001
    assert upload["latest_job_id"] == 73
    assert upload["error_code"] == "LOT_ID_REQUIRED"
    assert upload["action_required"] == "LOT_ID"
    assert client.get("/api/v1/production/cp/results").json()[0]["data_type"] == "CP"
    assert ("ENGINEERING", "CP") in service.calls
    assert ("PRODUCTION", "CP") in service.calls


def test_lists_reject_unknown_stage() -> None:
    response = _client(StubStageService()).get("/api/v1/production/xray/results")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TEST_STAGE_UNSUPPORTED"


def test_download_returns_stored_source_file(monkeypatch, tmp_path: Path) -> None:
    stored = tmp_path / "stored" / "sample.zip"
    stored.parent.mkdir()
    stored.write_bytes(b"original-zip-bytes")
    monkeypatch.setenv("TMS_UPLOAD_ROOT", str(tmp_path))
    service = StubStageService()
    service.stored_path = stored
    response = _client(service).get(
        "/api/v1/engineering/cp/uploads/41/files/9001/download"
    )
    assert response.status_code == 200
    assert response.content == b"original-zip-bytes"
    assert "sample.zip" in response.headers.get("content-disposition", "")


def test_download_rejects_legacy_unmanaged_source_path(
    monkeypatch, tmp_path: Path
) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"outside")
    monkeypatch.setenv("TMS_UPLOAD_ROOT", str(managed))
    service = StubStageService()
    service.stored_path = outside

    response = _client(service).get(
        "/api/v1/engineering/cp/uploads/41/files/9001/download"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "UPLOAD_FILE_STORAGE_UNMANAGED"


@pytest.mark.parametrize("configured_root", ["", ".", "relative\\raw"])
def test_download_fails_closed_for_empty_or_relative_managed_root(
    monkeypatch, tmp_path: Path, configured_root: str
) -> None:
    stored = tmp_path / "sample.zip"
    stored.write_bytes(b"sample")
    monkeypatch.setenv("TMS_UPLOAD_ROOT", configured_root)
    service = StubStageService()
    service.stored_path = stored

    with pytest.raises(RuntimeError, match="TMS_UPLOAD_ROOT"):
        _client(service).get(
            "/api/v1/engineering/cp/uploads/41/files/9001/download"
        )


def test_download_rejects_unknown_receipt(monkeypatch, tmp_path: Path) -> None:
    service = StubStageService()
    response = _client(service).get(
        "/api/v1/engineering/cp/uploads/41/files/9999/download"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UPLOAD_FILE_NOT_FOUND"


def test_reprocess_queues_same_route_a_pipeline(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    service = StubStageService()
    response = _client(service).post("/api/v1/production/cp/uploads/41/reprocess")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["job_id"] > 0
    assert body["cleaner_release"]["cleaner_release_id"] == 17
    assert service.queued == [41]
    assert service.archived == []


def test_sql_reprocess_uses_atomic_terminal_state_compare_and_set(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    service = StubStageService()
    client = _client(service)
    queue = AtomicInitialImportQueue()
    client.app.state.job_service = queue

    response = client.post("/api/v1/production/cp/uploads/41/reprocess")

    assert response.status_code == 200
    assert queue.allowed_statuses == [("PROCESSED", "FAILED")]
    assert service.queued == []


def test_reprocess_rejects_unknown_batch(monkeypatch, tmp_path: Path) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)

    class EmptyService(StubStageService):
        def get_batch_info(self, principal, business_domain, test_stage, batch_id):
            return None

    response = _client(EmptyService()).post(
        "/api/v1/production/cp/uploads/99/reprocess"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BATCH_NOT_FOUND"


@pytest.mark.parametrize("batch_status", ("QUEUED", "PROCESSING"))
def test_reprocess_rejects_active_batch(
    monkeypatch, tmp_path: Path, batch_status: str
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)

    class ActiveService(StubStageService):
        def get_batch_info(self, principal, business_domain, test_stage, batch_id):
            info = super().get_batch_info(
                principal, business_domain, test_stage, batch_id
            )
            return BatchInfo(
                info.import_batch_id,
                info.factory_code,
                batch_status,
                info.files,
            )

    service = ActiveService()
    response = _client(service).post(
        "/api/v1/production/cp/uploads/41/reprocess"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BATCH_ALREADY_ACTIVE"
    assert service.queued == []


def test_reprocess_needs_input_must_use_dedicated_resolution(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)

    class NeedsInputService(StubStageService):
        def get_batch_info(self, principal, business_domain, test_stage, batch_id):
            info = super().get_batch_info(
                principal, business_domain, test_stage, batch_id
            )
            return BatchInfo(
                info.import_batch_id,
                info.factory_code,
                "NEEDS_INPUT",
                info.files,
            )

    service = NeedsInputService()
    response = _client(service).post(
        "/api/v1/production/cp/uploads/41/reprocess"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LOT_INPUT_RESOLUTION_REQUIRED"
    assert service.queued == []


@pytest.mark.parametrize("batch_status", ("RECEIVED", "CANCELLED"))
def test_reprocess_only_accepts_processed_or_failed_batches(
    monkeypatch, tmp_path: Path, batch_status: str
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)

    class IneligibleService(StubStageService):
        def get_batch_info(self, principal, business_domain, test_stage, batch_id):
            info = super().get_batch_info(
                principal, business_domain, test_stage, batch_id
            )
            return BatchInfo(
                info.import_batch_id,
                info.factory_code,
                batch_status,
                info.files,
            )

    response = _client(IneligibleService()).post(
        "/api/v1/production/cp/uploads/41/reprocess"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BATCH_REPROCESS_NOT_ALLOWED"
