from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from app.core.errors import DomainError
from app.domain.auth import ALL_PERMISSIONS, Principal
from app.domain.cleaner_registry import CleanerRelease
from app.domain.data_domains import DataDomainRecord
from app.domain.quick_analysis import (
    InMemoryQuickAnalysisService,
    NewQuickAnalysisSession,
    QuickAnalysisArtifact,
)
from app.infrastructure.source_catalog import SourceCatalog, SourceRoot
from app.main import create_app
from fastapi.testclient import TestClient


class StubRegistry:
    def latest_released_for_contract(self, **contract: str) -> CleanerRelease:
        assert contract["test_stage"] in {"CP", "FT"}
        return CleanerRelease(
            21,
            8,
            contract["test_stage"],
            contract["factory_code"],
            contract["format_code"],
            "route-a-v1",
            contract["cleaner_code"],
            "v1",
            "0" * 64,
            "unused.pyz",
            "python.exe",
            "entrypoint",
            contract["adapter_code"],
            contract["input_contract_version"],
            contract["output_contract_version"],
            None,
            3600,
            10_000_000,
        )


class StubDataDomainService:
    def __init__(self, *domain_codes: str) -> None:
        self._domain_codes = domain_codes

    def list_for_principal(self, principal) -> tuple[DataDomainRecord, ...]:
        assert principal.user_id > 0
        return tuple(
            DataDomainRecord(
                data_domain_id=index,
                domain_code=code,
                domain_name=code,
                test_stage="FT",
                factory_code="JIEQUN",
                active=True,
            )
            for index, code in enumerate(self._domain_codes, start=1)
        )


class MutableDevelopmentAuth:
    def __init__(self, principal: Principal) -> None:
        self.principal = principal

    def principal_for_development(self) -> Principal:
        return self.principal


class RevokingQuickJobService:
    def __init__(self, grants: set[tuple[int, int]]) -> None:
        self.grants = grants
        self.principal_calls = 0

    def create_for_principal(self, request, principal):
        del request, principal
        self.principal_calls += 1
        self.grants.clear()
        raise DomainError("JOB_INPUT_NOT_FOUND", "任务输入不存在或无权访问", 404)


def _principal(user_id: int, name: str, *, admin: bool = False) -> Principal:
    return Principal(
        user_id,
        name,
        name,
        ("SYSTEM_ADMIN",) if admin else ("BUSINESS_USER",),
        ALL_PERMISSIONS if admin else frozenset({"ANALYSIS_RUN"}),
    )


def _session_request(
    *,
    source_root_code: str,
    access_scope: Literal["PERSONAL", "DOMAIN"],
    data_domain_id: int | None,
) -> NewQuickAnalysisSession:
    return NewQuickAnalysisSession(
        analysis_type="QUICK_PAT",
        test_stage="FT",
        factory_code="JIEQUN",
        source_root_code=source_root_code,
        source_relative_path="private-source",
        source_manifest_mode=(
            "LOCAL_PATH_SIZE_MTIME_V1"
            if source_root_code == "LOCAL_AGENT"
            else "PATH_SIZE_MTIME_V1"
        ),
        source_manifest_json="{}",
        source_manifest_sha256="c" * 64,
        source_file_count=1,
        source_total_bytes=10,
        retention_mode="RESULT_ONLY",
        cleaner_release_id=21,
        expires_at_utc=datetime.now(UTC) + timedelta(days=7),
        access_scope=access_scope,
        data_domain_id=data_domain_id,
        data_domain_code="JIEQUN_FT" if data_domain_id is not None else None,
    )


def test_quick_pat_api_queues_server_directory_without_uploading_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "shared" / "product-a"
    source.mkdir(parents=True)
    (source / "one.csv").write_text("value\n1\n", encoding="utf-8")
    app = create_app()
    app.state.cleaner_registry = StubRegistry()
    app.state.data_domain_service = StubDataDomainService("JIEQUN_FT")
    app.state.quick_analysis_service = InMemoryQuickAnalysisService(
        domain_grant_checker=lambda user_id, domain_id: user_id == 1 and domain_id == 1
    )
    app.state.source_catalog = SourceCatalog(
        (
            SourceRoot(
                "JIEQUN_SHARED",
                "杰群共享目录",
                tmp_path / "shared",
                "FT",
                "JIEQUN",
                (".csv",),
                data_domain_code="JIEQUN_FT",
            ),
        )
    )
    client = TestClient(app)

    roots = client.get("/api/v1/quick-analysis/source-roots")
    assert roots.status_code == 200
    assert roots.json()[0]["name"] == "杰群共享目录"
    assert roots.json()[0]["data_domain_code"] == "JIEQUN_FT"
    assert roots.json()[0]["data_domain_id"] == 1
    assert str(tmp_path) not in roots.text

    directories = client.get(
        "/api/v1/quick-analysis/source-roots/JIEQUN_SHARED/directories"
    )
    assert directories.status_code == 200, directories.text
    assert directories.json()["directories"][0]["relative_path"] == "product-a"

    preview = client.get(
        "/api/v1/quick-analysis/source-roots/JIEQUN_SHARED/manifest-preview",
        params={"relative_path": "product-a"},
    )
    assert preview.status_code == 200, preview.text
    manifest = preview.json()
    assert manifest["recursive"] is True
    assert manifest["file_count"] == 1
    assert manifest["relative_path"] == "product-a"
    assert manifest["allowed_suffixes"] == [".csv"]
    assert str(tmp_path) not in preview.text

    created = client.post(
        "/api/v1/quick-analysis/pat",
        json={
            "source_root_code": "JIEQUN_SHARED",
            "source_relative_path": "product-a",
            "source_manifest_mode": manifest["mode"],
            "source_manifest_sha256": manifest["sha"],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["analysis_type"] == "QUICK_PAT"
    assert body["source_file_count"] == 1
    assert body["source_relative_path"] == "product-a"
    assert body["job_id"] == 1
    assert body["status"] == "QUEUED"
    assert body["access_scope"] == "DOMAIN"
    assert body["data_domain_id"] == 1
    assert body["reserved_bytes"] >= 64 * 1024**2
    assert str(tmp_path) not in created.text

    listed = client.get("/api/v1/quick-analysis/sessions")
    assert listed.status_code == 200
    assert (
        listed.json()["items"][0]["analysis_session_id"] == body["analysis_session_id"]
    )
    assert listed.json()["total"] == 1


def test_direct_path_api_previews_and_queues_personal_pat(tmp_path: Path) -> None:
    source = tmp_path / "520data" / "NCEAP020N10LL"
    source.mkdir(parents=True)
    (source / "one.csv").write_text("value\n1\n", encoding="utf-8")
    app = create_app()
    app.state.cleaner_registry = StubRegistry()
    app.state.quick_analysis_service = InMemoryQuickAnalysisService()
    client = TestClient(app)

    preview = client.post(
        "/api/v1/quick-analysis/direct-path/preview",
        json={"path": str(source), "tool_code": "JIEQUN_FT_QUICK_PAT_EXISTING"},
    )

    assert preview.status_code == 200, preview.text
    manifest = preview.json()
    assert manifest["path"] == str(source.resolve())
    assert manifest["source_label"] == "NCEAP020N10LL"
    assert manifest["file_count"] == 1
    assert manifest["mode"] == "LOCAL_PATH_SIZE_MTIME_V1"

    created = client.post(
        "/api/v1/quick-analysis/direct-path/pat",
        json={
            "path": str(source),
            "tool_code": "JIEQUN_FT_QUICK_PAT_EXISTING",
            "source_manifest_mode": manifest["mode"],
            "source_manifest_sha256": manifest["sha"],
        },
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["access_scope"] == "PERSONAL"
    assert body["source_root_code"] == "LOCAL_AGENT"
    assert body["source_relative_path"] == str(source.resolve())
    assert body["status"] == "QUEUED"
    assert body["job_id"] == 1


def test_direct_path_api_previews_and_queues_cp_factory_pat(tmp_path: Path) -> None:
    source = tmp_path / "cp" / "JETECH_LOT"
    source.mkdir(parents=True)
    (source / "wafer.xlsx").write_bytes(b"xlsx-fixture")
    app = create_app()
    app.state.cleaner_registry = StubRegistry()
    app.state.quick_analysis_service = InMemoryQuickAnalysisService()
    client = TestClient(app)
    tool_code = "JETECH_CP_QUICK_PAT_EXISTING"

    preview = client.post(
        "/api/v1/quick-analysis/direct-path/preview",
        json={"path": str(source), "tool_code": tool_code},
    )
    assert preview.status_code == 200, preview.text
    manifest = preview.json()
    assert manifest["test_stage"] == "CP"
    assert manifest["factory_code"] == "JETECH"
    assert manifest["allowed_suffixes"] == [".xls", ".xlsx"]

    created = client.post(
        "/api/v1/quick-analysis/direct-path/pat",
        json={
            "path": str(source),
            "tool_code": tool_code,
            "source_manifest_mode": manifest["mode"],
            "source_manifest_sha256": manifest["sha"],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["test_stage"] == "CP"
    assert body["factory_code"] == "JETECH"
    assert body["access_scope"] == "PERSONAL"


@pytest.mark.parametrize(
    ("tool_code", "factory_code", "file_name", "allowed_suffixes"),
    [
        (
            "RIYUEXIN_FT_QUICK_PAT_EXISTING",
            "RIYUEXIN",
            "wafer.xlsx",
            [".xlsx"],
        ),
        (
            "DIANJI_FT_QUICK_PAT_EXISTING",
            "DIANJI",
            "raw.xls",
            [".xls", ".xlsx", ".csv"],
        ),
    ],
)
def test_direct_path_api_previews_and_queues_additional_ft_pat(
    tmp_path: Path,
    tool_code: str,
    factory_code: str,
    file_name: str,
    allowed_suffixes: list[str],
) -> None:
    source = tmp_path / factory_code / "product"
    source.mkdir(parents=True)
    (source / file_name).write_bytes(b"raw-ft-fixture")
    app = create_app()
    app.state.cleaner_registry = StubRegistry()
    app.state.quick_analysis_service = InMemoryQuickAnalysisService()
    client = TestClient(app)

    preview = client.post(
        "/api/v1/quick-analysis/direct-path/preview",
        json={"path": str(source), "tool_code": tool_code},
    )
    assert preview.status_code == 200, preview.text
    manifest = preview.json()
    assert manifest["test_stage"] == "FT"
    assert manifest["factory_code"] == factory_code
    assert manifest["allowed_suffixes"] == allowed_suffixes

    created = client.post(
        "/api/v1/quick-analysis/direct-path/pat",
        json={
            "path": str(source),
            "tool_code": tool_code,
            "source_manifest_mode": manifest["mode"],
            "source_manifest_sha256": manifest["sha"],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["test_stage"] == "FT"
    assert body["factory_code"] == factory_code
    assert body["access_scope"] == "PERSONAL"


def test_direct_path_api_rejects_changed_preview(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.csv").write_text("value\n1\n", encoding="utf-8")
    app = create_app()
    app.state.cleaner_registry = StubRegistry()
    app.state.quick_analysis_service = InMemoryQuickAnalysisService()
    client = TestClient(app)
    preview = client.post(
        "/api/v1/quick-analysis/direct-path/preview", json={"path": str(source)}
    ).json()
    (source / "two.csv").write_text("value\n2\n", encoding="utf-8")

    response = client.post(
        "/api/v1/quick-analysis/direct-path/pat",
        json={
            "path": str(source),
            "source_manifest_mode": preview["mode"],
            "source_manifest_sha256": preview["sha"],
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "QUICK_SOURCE_CHANGED"


def test_quick_pat_rejects_a_changed_or_unconfirmed_manifest(tmp_path: Path) -> None:
    source = tmp_path / "shared" / "product-a"
    source.mkdir(parents=True)
    (source / "one.csv").write_text("value\n1\n", encoding="utf-8")
    app = create_app()
    app.state.cleaner_registry = StubRegistry()
    app.state.data_domain_service = StubDataDomainService("JIEQUN_FT")
    app.state.quick_analysis_service = InMemoryQuickAnalysisService(
        domain_grant_checker=lambda user_id, domain_id: user_id == 1 and domain_id == 1
    )
    app.state.source_catalog = SourceCatalog(
        (
            SourceRoot(
                "JIEQUN_SHARED",
                "杰群共享目录",
                tmp_path / "shared",
                "FT",
                "JIEQUN",
                (".csv",),
                data_domain_code="JIEQUN_FT",
            ),
        )
    )
    client = TestClient(app)
    response = client.post(
        "/api/v1/quick-analysis/pat",
        json={
            "source_root_code": "JIEQUN_SHARED",
            "source_relative_path": "product-a",
            "source_manifest_mode": "PATH_SIZE_MTIME_V1",
            "source_manifest_sha256": "0" * 64,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "QUICK_SOURCE_CHANGED"


@pytest.mark.parametrize(
    ("data_domain_code", "granted_codes"),
    [
        (None, ("JIEQUN_FT",)),
        ("JIEQUN_FT", ()),
    ],
    ids=("unbound-root", "ungranted-domain"),
)
def test_quick_source_catalog_fails_closed_without_an_active_domain_grant(
    tmp_path: Path,
    data_domain_code: str | None,
    granted_codes: tuple[str, ...],
) -> None:
    source = tmp_path / "shared" / "product-a"
    source.mkdir(parents=True)
    (source / "one.csv").write_text("value\n1\n", encoding="utf-8")
    app = create_app()
    app.state.cleaner_registry = StubRegistry()
    app.state.data_domain_service = StubDataDomainService(*granted_codes)
    app.state.source_catalog = SourceCatalog(
        (
            SourceRoot(
                "JIEQUN_SECRET",
                "不应泄露的数据源",
                tmp_path / "shared",
                "FT",
                "JIEQUN",
                (".csv",),
                data_domain_code=data_domain_code,
            ),
        )
    )
    client = TestClient(app)

    roots = client.get("/api/v1/quick-analysis/source-roots")
    assert roots.status_code == 200
    assert roots.json() == []
    assert "不应泄露的数据源" not in roots.text

    responses = (
        client.get("/api/v1/quick-analysis/source-roots/JIEQUN_SECRET/directories"),
        client.get(
            "/api/v1/quick-analysis/source-roots/JIEQUN_SECRET/manifest-preview",
            params={"relative_path": "product-a"},
        ),
        client.post(
            "/api/v1/quick-analysis/pat",
            json={
                "source_root_code": "JIEQUN_SECRET",
                "source_relative_path": "product-a",
                "source_manifest_mode": "PATH_SIZE_MTIME_V1",
                "source_manifest_sha256": "0" * 64,
            },
        ),
    )
    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "SOURCE_ROOT_NOT_FOUND"
        assert response.json()["error"]["message"] == "数据源不存在或当前账户无权访问"
        assert "不应泄露的数据源" not in response.text

    missing = client.get(
        "/api/v1/quick-analysis/source-roots/DOES_NOT_EXIST/directories"
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SOURCE_ROOT_NOT_FOUND"
    assert missing.json()["error"]["message"] == responses[0].json()["error"]["message"]


def test_quick_session_api_enforces_personal_and_live_domain_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _principal(10, "owner")
    member = _principal(11, "member")
    outsider = _principal(12, "outsider")
    admin = _principal(99, "admin", admin=True)
    grant_expiry = {
        (owner.user_id, 7): datetime.now(UTC) + timedelta(hours=1),
        (member.user_id, 7): datetime.now(UTC) + timedelta(hours=1),
    }
    service = InMemoryQuickAnalysisService(
        domain_grant_checker=lambda user_id, domain_id: (
            (expiry := grant_expiry.get((user_id, domain_id))) is not None
            and expiry > datetime.now(UTC)
        )
    )
    personal = service.create(
        owner,
        _session_request(
            source_root_code="LOCAL_AGENT",
            access_scope="PERSONAL",
            data_domain_id=None,
        ),
    )
    domain = service.create(
        owner,
        _session_request(
            source_root_code="JIEQUN_SHARED",
            access_scope="DOMAIN",
            data_domain_id=7,
        ),
    )
    for session, job_id, payload in (
        (personal, 81, b"personal"),
        (domain, 82, b"domain"),
    ):
        report = tmp_path / str(job_id) / "PAT.xlsx"
        report.parent.mkdir(parents=True)
        report.write_bytes(payload)
        service.attach_job(session.analysis_session_id, job_id)
        service.mark_running(session.analysis_session_id)
        service.record_success(
            session.analysis_session_id,
            job_id,
            parameter_count=1,
            record_count=1,
            summary={},
            artifacts=(
                QuickAnalysisArtifact(
                    "pat_report",
                    str(report),
                    report.stat().st_size,
                    hashlib.sha256(payload).hexdigest(),
                ),
            ),
        )

    app = create_app()
    auth = MutableDevelopmentAuth(owner)
    app.state.auth_service = auth
    app.state.quick_analysis_service = service
    monkeypatch.setenv("TMS_QUICK_WORK_ROOT", str(tmp_path))
    client = TestClient(app)

    owner_personal = client.get(
        "/api/v1/quick-analysis/sessions", params={"access_scope": "PERSONAL"}
    )
    assert owner_personal.status_code == 200
    assert [item["analysis_session_id"] for item in owner_personal.json()["items"]] == [
        personal.analysis_session_id
    ]

    auth.principal = member
    member_list = client.get("/api/v1/quick-analysis/sessions")
    assert member_list.status_code == 200
    assert [item["analysis_session_id"] for item in member_list.json()["items"]] == [
        domain.analysis_session_id
    ]
    assert (
        client.get(
            f"/api/v1/quick-analysis/sessions/{domain.analysis_session_id}"
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/quick-analysis/sessions/{domain.analysis_session_id}/download"
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/quick-analysis/sessions/{personal.analysis_session_id}"
        ).status_code
        == 404
    )

    for denied in (outsider, admin):
        auth.principal = denied
        assert client.get("/api/v1/quick-analysis/sessions").json()["total"] == 0
        assert (
            client.get(
                f"/api/v1/quick-analysis/sessions/{domain.analysis_session_id}"
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/quick-analysis/sessions/{domain.analysis_session_id}/download"
            ).status_code
            == 404
        )

    auth.principal = member
    grant_expiry[(member.user_id, 7)] = datetime.now(UTC) - timedelta(seconds=1)
    assert client.get("/api/v1/quick-analysis/sessions").json()["total"] == 0
    assert (
        client.get(
            f"/api/v1/quick-analysis/sessions/{domain.analysis_session_id}"
        ).status_code
        == 404
    )


def test_server_quick_rechecks_grant_when_queue_job_is_created(tmp_path: Path) -> None:
    source = tmp_path / "shared" / "product-a"
    source.mkdir(parents=True)
    (source / "one.csv").write_text("value\n1\n", encoding="utf-8")
    grants = {(1, 1)}
    app = create_app()
    app.state.cleaner_registry = StubRegistry()
    app.state.data_domain_service = StubDataDomainService("JIEQUN_FT")
    app.state.quick_analysis_service = InMemoryQuickAnalysisService(
        domain_grant_checker=lambda user_id, domain_id: (user_id, domain_id) in grants
    )
    app.state.source_catalog = SourceCatalog(
        (
            SourceRoot(
                "JIEQUN_SHARED",
                "杰群共享目录",
                tmp_path / "shared",
                "FT",
                "JIEQUN",
                (".csv",),
                data_domain_code="JIEQUN_FT",
            ),
        )
    )
    jobs = RevokingQuickJobService(grants)
    app.state.job_service = jobs
    client = TestClient(app)
    preview = client.get(
        "/api/v1/quick-analysis/source-roots/JIEQUN_SHARED/manifest-preview",
        params={"relative_path": "product-a"},
    ).json()

    response = client.post(
        "/api/v1/quick-analysis/pat",
        json={
            "source_root_code": "JIEQUN_SHARED",
            "source_relative_path": "product-a",
            "source_manifest_mode": preview["mode"],
            "source_manifest_sha256": preview["sha"],
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_INPUT_NOT_FOUND"
    assert jobs.principal_calls == 1
    grants.add((1, 1))
    sessions = client.get("/api/v1/quick-analysis/sessions").json()["items"]
    assert len(sessions) == 1
    assert sessions[0]["status"] == "FAILED"
    assert sessions[0]["job_id"] is None
    assert sessions[0]["cleanup_status"] == "CLEANED"
