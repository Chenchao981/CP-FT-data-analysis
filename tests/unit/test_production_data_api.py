from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.production_data import ProductionResultRow, ProductionUploadRow
from app.infrastructure.existing_cleaner_runner import CleanerArtifact, ExistingCleanerRunResult
from app.main import create_app


class StubProductionService:
    def __init__(self) -> None:
        self.principal = None

    def register_upload(self, principal, factory_code, files, remark):
        self.principal = principal
        assert factory_code == "huahong"
        assert files[0].sha256
        return 41

    def mark_processing(self, batch_id, principal):
        assert batch_id == 41
        assert principal.user_id == 1
        return 73

    def record_cp_result(self, batch_id, job_id, result):
        assert result["lot_id"] == "FA5X-2565"
        assert result["product_name"] == "NCETEN30CAC"

    def mark_failed(self, batch_id, job_id, message):
        raise AssertionError(message)

    def list_uploads(self, principal):
        return (ProductionUploadRow(41, 1, "sample.zip", "zip", 100, "huahong", "2026-08-21T00:00:00", None, principal.login_name, principal.display_name, "RECEIVED"),)

    def list_results(self, principal):
        return (ProductionResultRow(1, 41, "FA5X-2565", "NCETEN30CAC", "FA5X-2565", 1, "huahong", 2, 10, 9, .9, "PROCESSED", "CP", "2026-08-21T00:00:00"),)


def test_cp_upload_uses_authenticated_principal_and_existing_cleaner(monkeypatch, tmp_path: Path) -> None:
    cleaned = tmp_path / "FA5X-2565_cleaned_20260821_000000.csv"
    cleaned.write_text("Lot_ID,Wafer_ID,Seq,Bin,X,Y,VTH,RDSON\nFA5X-2565,1,1,1,1,1,4,0.1\n", encoding="utf-8")
    yield_file = tmp_path / "FA5X-2565_yield_20260821_000000.csv"
    yield_file.write_text("Product_Name,Lot_ID,Wafer_ID,Yield,Total,Pass\nNCETEN30CAC,FA5X-2565,1,90%,10,9\nNCETEN30CAC,ALL,ALL,90%,10,9\n", encoding="utf-8")
    result = ExistingCleanerRunResult("CP", "huahong", str(tmp_path), (CleanerArtifact("cleaned", str(cleaned), cleaned.stat().st_size), CleanerArtifact("yield", str(yield_file), yield_file.stat().st_size)), "ok")

    class StubRunner:
        def run(self, **kwargs):
            assert kwargs["test_stage"] == "CP"
            return result

    monkeypatch.setenv("TMS_UPLOAD_ROOT", str(tmp_path / "raw"))
    monkeypatch.setattr("app.api.production_data.ExistingCleanerRunner", StubRunner)
    app = create_app()
    service = StubProductionService()
    app.state.production_data_service = service
    client = TestClient(app)
    response = client.post("/api/v1/production/cp/uploads", files={"files": ("sample.zip", b"sample", "application/zip")}, data={"factory_code": "huahong"})
    assert response.status_code == 201
    assert response.json()["uploader"]["user_id"] == 1
    assert service.principal.login_name == "development-admin"


def test_cp_lists_are_scoped_through_current_principal() -> None:
    app = create_app()
    app.state.production_data_service = StubProductionService()
    client = TestClient(app)
    assert client.get("/api/v1/production/cp/uploads").json()[0]["uploader_login"] == "development-admin"
    assert client.get("/api/v1/production/cp/results").json()[0]["data_type"] == "CP"
