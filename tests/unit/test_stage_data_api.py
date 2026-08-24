from __future__ import annotations

from pathlib import Path

from app.domain.cleaner_registry import CleanerRelease
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
from app.main import create_app
from fastapi.testclient import TestClient


class StubStageService:
    def __init__(self) -> None:
        self.principal = None
        self.calls: list[tuple[str, str]] = []
        self.archived: list[int] = []
        self.stored_path = Path("unused")
        self.queued: list[int] = []

    def register_upload(
        self, principal, business_domain, test_stage, factory_code, files, remark
    ):
        self.principal = principal
        self.calls.append((business_domain, test_stage))
        assert business_domain in {"ENGINEERING", "PRODUCTION"}
        assert test_stage in {"CP", "FT"}
        assert factory_code in {"huahong", "riyuexin"}
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
                "RECEIVED",
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
                "2026-08-21T00:00:00",
            ),
        )

    def get_batch_info(self, principal, business_domain, test_stage, batch_id):
        assert batch_id == 41
        return BatchInfo(
            41,
            "huahong",
            "PROCESSED",
            (BatchFileInfo(9001, "sample.zip", str(self.stored_path)),),
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
    monkeypatch.setattr("app.api.stage_data.ExistingCleanerRunner", StubRunner)


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
    monkeypatch.setattr("app.api.stage_data.ExistingCleanerRunner", StubRunner)
    return seen


def _client(service: StubStageService) -> TestClient:
    app = create_app()
    app.state.stage_data_service = service
    app.state.cleaner_registry = StubCleanerRegistry()
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
    assert (
        client.get("/api/v1/engineering/cp/uploads").json()[0]["uploader_login"]
        == "development-admin"
    )
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
    service = StubStageService()
    service.stored_path = stored
    response = _client(service).get(
        "/api/v1/engineering/cp/uploads/41/files/9001/download"
    )
    assert response.status_code == 200
    assert response.content == b"original-zip-bytes"
    assert "sample.zip" in response.headers.get("content-disposition", "")


def test_download_rejects_unknown_receipt(monkeypatch, tmp_path: Path) -> None:
    service = StubStageService()
    response = _client(service).get(
        "/api/v1/engineering/cp/uploads/41/files/9999/download"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UPLOAD_FILE_NOT_FOUND"


def test_reprocess_reruns_cleaner_and_archives_previous_results(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_cp_cleaner(monkeypatch, tmp_path)
    service = StubStageService()
    response = _client(service).post("/api/v1/production/cp/uploads/41/reprocess")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PROCESSED"
    assert body["result"]["lot_id"] == "FA5X-2565"
    assert service.archived == [41]


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
