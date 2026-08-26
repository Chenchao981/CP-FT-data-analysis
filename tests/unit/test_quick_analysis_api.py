from __future__ import annotations

from pathlib import Path

from app.domain.cleaner_registry import CleanerRelease
from app.infrastructure.source_catalog import SourceCatalog, SourceRoot
from app.main import create_app
from fastapi.testclient import TestClient


class StubRegistry:
    def latest_released(self, test_stage: str, factory_code: str) -> CleanerRelease:
        assert (test_stage, factory_code) == ("FT", "JIEQUN")
        return CleanerRelease(
            21,
            8,
            "FT",
            "JIEQUN",
            "JIEQUN_FT_QUICK_PAT_EXISTING",
            "route-a-v1",
            "JIEQUN_FT_QUICK_PAT_EXISTING",
            "v1",
            "0" * 64,
            "unused.pyz",
            "python.exe",
            "entrypoint",
            "JIEQUN_FT_QUICK_PAT_PYZ",
            "JIEQUN_UNIFIED_CSV_DIRECTORY_V1",
            "FT_PAT_RESULT_V1",
            None,
            3600,
            10_000_000,
        )


def test_quick_pat_api_queues_server_directory_without_uploading_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "shared" / "product-a"
    source.mkdir(parents=True)
    (source / "one.csv").write_text("value\n1\n", encoding="utf-8")
    app = create_app()
    app.state.cleaner_registry = StubRegistry()
    app.state.source_catalog = SourceCatalog(
        (
            SourceRoot(
                "JIEQUN_SHARED",
                "杰群共享目录",
                tmp_path / "shared",
                "FT",
                "JIEQUN",
                (".csv",),
            ),
        )
    )
    client = TestClient(app)

    roots = client.get("/api/v1/quick-analysis/source-roots")
    assert roots.status_code == 200
    assert roots.json()[0]["name"] == "杰群共享目录"
    assert str(tmp_path) not in roots.text

    created = client.post(
        "/api/v1/quick-analysis/pat",
        json={
            "source_root_code": "JIEQUN_SHARED",
            "source_relative_path": "product-a",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["analysis_type"] == "QUICK_PAT"
    assert body["source_file_count"] == 1
    assert body["source_relative_path"] == "product-a"
    assert body["job_id"] == 1
    assert body["status"] == "QUEUED"
    assert str(tmp_path) not in created.text

    listed = client.get("/api/v1/quick-analysis/sessions")
    assert listed.status_code == 200
    assert listed.json()[0]["analysis_session_id"] == body["analysis_session_id"]
