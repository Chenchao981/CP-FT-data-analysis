from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.stage_data import StageResultRow, StageUploadRow
from app.infrastructure.existing_cleaner_runner import CleanerArtifact, ExistingCleanerRunResult
from app.main import create_app


class StubStageService:
    def __init__(self) -> None:
        self.principal = None
        self.calls: list[tuple[str, str]] = []

    def register_upload(self, principal, business_domain, test_stage, factory_code, files, remark):
        self.principal = principal
        self.calls.append((business_domain, test_stage))
        assert business_domain in {"ENGINEERING", "PRODUCTION"}
        assert test_stage == "CP"
        assert factory_code == "huahong"
        assert files[0].sha256
        return 41

    def mark_processing(self, batch_id, principal):
        assert batch_id == 41
        assert principal.user_id == 1
        return 73

    def record_result(self, batch_id, job_id, result):
        assert result["lot_id"] == "FA5X-2565"
        assert result["product_name"] == "NCETEN30CAC"
        assert result["data_type"] == "CP"

    def mark_failed(self, batch_id, job_id, message):
        raise AssertionError(message)

    def list_uploads(self, principal, business_domain, test_stage):
        self.calls.append((business_domain, test_stage))
        return (StageUploadRow(41, 1, "sample.zip", "zip", 100, "huahong", "2026-08-21T00:00:00", None, principal.login_name, principal.display_name, "RECEIVED"),)

    def list_results(self, principal, business_domain, test_stage):
        self.calls.append((business_domain, test_stage))
        return (StageResultRow(1, 41, "FA5X-2565", "NCETEN30CAC", "FA5X-2565", 1, "huahong", 2, 10, 9, .9, "PROCESSED", "CP", "2026-08-21T00:00:00"),)


def _stub_cleaner(monkeypatch, tmp_path: Path) -> None:
    cleaned = tmp_path / "FA5X-2565_cleaned_20260821_000000.csv"
    cleaned.write_text("Lot_ID,Wafer_ID,Seq,Bin,X,Y,VTH,RDSON\nFA5X-2565,1,1,1,1,1,4,0.1\n", encoding="utf-8")
    yield_file = tmp_path / "FA5X-2565_yield_20260821_000000.csv"
    yield_file.write_text("Product_Name,Lot_ID,Wafer_ID,Yield,Total,Pass\nNCETEN30CAC,FA5X-2565,1,90%,10,9\nNCETEN30CAC,ALL,ALL,90%,10,9\n", encoding="utf-8")
    result = ExistingCleanerRunResult("CP", "huahong", str(tmp_path), (CleanerArtifact("cleaned", str(cleaned), cleaned.stat().st_size), CleanerArtifact("yield", str(yield_file), yield_file.stat().st_size)), "ok")

    class StubRunner:
        def run(self, **kwargs):
            assert kwargs["test_stage"] == "CP"
            assert kwargs["factory"] == "huahong"
            return result

    monkeypatch.setenv("TMS_UPLOAD_ROOT", str(tmp_path / "raw"))
    monkeypatch.setattr("app.api.stage_data.ExistingCleanerRunner", StubRunner)


def _client(service: StubStageService) -> TestClient:
    app = create_app()
    app.state.stage_data_service = service
    return TestClient(app)


def test_engineering_cp_upload_uses_authenticated_principal_and_existing_cleaner(monkeypatch, tmp_path: Path) -> None:
    _stub_cleaner(monkeypatch, tmp_path)
    service = StubStageService()
    response = _client(service).post("/api/v1/engineering/cp/uploads", files={"files": ("sample.zip", b"sample", "application/zip")}, data={"factory_code": "huahong"})
    assert response.status_code == 201
    body = response.json()
    assert body["business_domain"] == "ENGINEERING"
    assert body["test_stage"] == "CP"
    assert body["uploader"]["user_id"] == 1
    assert service.principal.login_name == "development-admin"


def test_production_cp_upload_keeps_existing_behavior(monkeypatch, tmp_path: Path) -> None:
    _stub_cleaner(monkeypatch, tmp_path)
    service = StubStageService()
    response = _client(service).post("/api/v1/production/cp/uploads", files={"files": ("sample.zip", b"sample", "application/zip")}, data={"factory_code": "huahong"})
    assert response.status_code == 201
    assert service.calls == [("PRODUCTION", "CP")]
    assert response.json()["result"]["unit_count"] == 10


def test_upload_rejects_unsupported_test_stage(monkeypatch, tmp_path: Path) -> None:
    _stub_cleaner(monkeypatch, tmp_path)
    response = _client(StubStageService()).post("/api/v1/production/ft/uploads", files={"files": ("sample.xlsx", b"sample", "application/octet-stream")}, data={"factory_code": "riyuexin"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "STAGE_UPLOAD_UNSUPPORTED"


def test_upload_rejects_unknown_business_domain(monkeypatch, tmp_path: Path) -> None:
    _stub_cleaner(monkeypatch, tmp_path)
    response = _client(StubStageService()).post("/api/v1/pilot/cp/uploads", files={"files": ("sample.zip", b"sample", "application/zip")}, data={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BUSINESS_DOMAIN_UNSUPPORTED"


def test_lists_are_scoped_by_business_domain_and_stage() -> None:
    service = StubStageService()
    client = _client(service)
    assert client.get("/api/v1/engineering/cp/uploads").json()[0]["uploader_login"] == "development-admin"
    assert client.get("/api/v1/production/cp/results").json()[0]["data_type"] == "CP"
    assert ("ENGINEERING", "CP") in service.calls
    assert ("PRODUCTION", "CP") in service.calls


def test_lists_reject_unknown_stage() -> None:
    response = _client(StubStageService()).get("/api/v1/production/xray/results")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TEST_STAGE_UNSUPPORTED"
