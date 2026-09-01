from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.api.dependencies import current_principal
from app.domain.auth import DEVELOPMENT_PRINCIPAL, Principal
from app.domain.cleaner_registry import CleanerRelease
from app.domain.jobs import JobStatus
from app.domain.quick_analysis import (
    InMemoryQuickAnalysisService,
    NewQuickAnalysisSession,
    QuickAnalysisArtifact,
    QuickAnalysisStatus,
)
from app.domain.quick_capacity import QuickCapacityPolicy
from app.infrastructure import local_quick_result
from app.infrastructure.local_quick_result import LocalQuickResultStore
from app.main import create_app
from fastapi.testclient import TestClient
from openpyxl import Workbook

PAT_HEADERS = (
    "统计量",
    "总计数",
    "均值",
    "标准差",
    "最小值",
    "下四分位数",
    "中位数",
    "上四分位数",
    "最大值",
    "Sigma",
    "LCL\n计算值",
    "UCL\n计算值",
    "LCL\n更新前",
    "UCL\n更新前",
    "LCL\n更新后",
    "UCL\n更新后",
    "是否\n更新",
)


class StubRegistry:
    def __init__(self, release: CleanerRelease) -> None:
        self.release = release

    def latest_released_for_contract(self, **contract: str) -> CleanerRelease:
        assert contract == {
            "test_stage": "FT",
            "factory_code": "JIEQUN",
            "format_code": "JIEQUN_FT_QUICK_PAT_EXISTING",
            "cleaner_code": "JIEQUN_FT_QUICK_PAT_EXISTING",
            "adapter_code": "JIEQUN_FT_QUICK_PAT_PYZ",
            "input_contract_version": "JIEQUN_UNIFIED_CSV_DIRECTORY_V1",
            "output_contract_version": "FT_PAT_RESULT_V1",
        }
        return self.release


def _release() -> CleanerRelease:
    return CleanerRelease(
        cleaner_release_id=21,
        format_profile_id=8,
        test_stage="FT",
        factory_code="JIEQUN",
        format_code="JIEQUN_FT_QUICK_PAT_EXISTING",
        profile_version="route-a-v1",
        cleaner_code="JIEQUN_FT_QUICK_PAT_EXISTING",
        cleaner_version="v2.15.0",
        code_checksum="a" * 64,
        artifact_uri=r"F:\secret\release\ft_data_cleaner.pyz",
        runtime_uri=r"D:\secret\python.exe",
        entrypoint="factories.jiequn.pat_cleaner.generate_raw_pat",
        adapter_code="JIEQUN_FT_QUICK_PAT_PYZ",
        input_contract_version="JIEQUN_UNIFIED_CSV_DIRECTORY_V1",
        output_contract_version="FT_PAT_RESULT_V1",
        execution_config_json=None,
        timeout_seconds=3_600,
        max_output_bytes=2_000_000,
    )


def _policy(work_root: Path) -> QuickCapacityPolicy:
    return QuickCapacityPolicy(
        global_capacity_bytes=8_000_000,
        user_capacity_bytes=4_000_000,
        minimum_free_bytes=0,
        reserve_ratio=0.5,
        reserve_overhead_bytes=31,
        work_root=work_root,
    )


def _app(tmp_path: Path):
    app = create_app()
    policy = _policy(tmp_path / "quick-work")
    app.state.cleaner_registry = StubRegistry(_release())
    app.state.quick_capacity_policy = policy
    app.state.quick_analysis_service = InMemoryQuickAnalysisService(capacity=policy)
    return app


def _pat_xlsx(
    parameter_names: tuple[str, ...] = ("VTH", "RDON"),
    *,
    formula: bool = False,
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "PAT"
    sheet.append(PAT_HEADERS)
    sheet.append(("变量", *PAT_HEADERS[1:]))
    for index, parameter in enumerate(parameter_names):
        row = [
            parameter,
            100 - index,
            "=2+2" if formula and index == 0 else 4.1,
            0.2,
            3.1,
            3.9,
            4.1,
            4.3,
            5.0,
            0.3,
            2.3,
            5.9,
            None,
            None,
            None,
            None,
            None,
        ]
        sheet.append(row)
    target = BytesIO()
    workbook.save(target)
    workbook.close()
    return target.getvalue()


def _pat_xlsx_with_hidden_raw_part() -> bytes:
    target = BytesIO(_pat_xlsx())
    with ZipFile(target, mode="a", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "customXml/raw-source.csv",
            "lot_id,secret\nLOT-A,RAW-MEASUREMENT\n",
        )
    return target.getvalue()


def _receipt(report: bytes, *, parameter_count: int = 2) -> dict[str, object]:
    return {
        "contract_version": "TMS_LOCAL_RESULT_V1",
        "tool_code": "JIEQUN_FT_QUICK_PAT_EXISTING",
        "analysis_type": "QUICK_PAT",
        "test_stage": "FT",
        "factory_code": "JIEQUN",
        "release_sha256": "A" * 64,
        "source_label": "NCEAP020N10LL-520data",
        "manifest": {
            "mode": "LOCAL_PATH_SIZE_MTIME_V1",
            "sha256": "B" * 64,
            "file_count": 520,
            "total_bytes": 4_000_000_000,
        },
        "summary": {
            "parameter_count": parameter_count,
            "record_count": 6_813_800,
            "elapsed_seconds": 127.745,
        },
        "result": {
            "filename": "PAT_001.xlsx",
            "size_bytes": len(report),
            "sha256": hashlib.sha256(report).hexdigest(),
        },
    }


def test_local_capability_exposes_exact_release_contract_without_local_paths(
    tmp_path: Path,
) -> None:
    response = TestClient(_app(tmp_path)).get("/api/v1/quick-analysis/local-capability")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["contract_version"] == "TMS_LOCAL_RESULT_V1"
    assert body["tool_code"] == "JIEQUN_FT_QUICK_PAT_EXISTING"
    assert body["release"] == {
        "cleaner_release_id": 21,
        "format_profile_id": 8,
        "format_code": "JIEQUN_FT_QUICK_PAT_EXISTING",
        "profile_version": "route-a-v1",
        "cleaner_code": "JIEQUN_FT_QUICK_PAT_EXISTING",
        "cleaner_version": "v2.15.0",
        "sha256": "a" * 64,
        "entrypoint": "factories.jiequn.pat_cleaner.generate_raw_pat",
        "adapter_code": "JIEQUN_FT_QUICK_PAT_PYZ",
        "input_contract_version": "JIEQUN_UNIFIED_CSV_DIRECTORY_V1",
        "output_contract_version": "FT_PAT_RESULT_V1",
        "timeout_seconds": 3_600,
        "max_output_bytes": 2_000_000,
    }
    assert body["upload"] == {
        "multipart_receipt_field": "receipt_json",
        "multipart_result_field": "result_file",
        "accepted_extension": ".xlsx",
    }
    assert "artifact_uri" not in response.text
    assert "runtime_uri" not in response.text
    assert "secret" not in response.text


def test_local_capability_fails_closed_for_an_unapproved_release_adapter(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    app.state.cleaner_registry = StubRegistry(
        replace(_release(), adapter_code="UNAPPROVED_ADAPTER")
    )
    response = TestClient(app).get("/api/v1/quick-analysis/local-capability")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ("LOCAL_QUICK_CAPABILITY_UNAVAILABLE")


def test_local_result_creates_owner_only_success_session_and_atomic_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    app = _app(tmp_path)
    work_root = app.state.quick_capacity_policy.work_root
    monkeypatch.setenv("TMS_QUICK_WORK_ROOT", str(work_root))
    report = _pat_xlsx()
    response = TestClient(app).post(
        "/api/v1/quick-analysis/local-results",
        data={"receipt_json": json.dumps(_receipt(report), ensure_ascii=False)},
        files={
            "result_file": (
                "PAT_001.xlsx",
                report,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["parameter_count"] == 2
    assert body["record_count"] == 6_813_800
    assert body["summary"]["elapsed_seconds"] == 127.745
    assert body["summary"]["parameter_count"] == 2
    assert body["summary"]["record_count"] == 6_813_800
    assert body["reserved_bytes"] == _release().max_output_bytes + 31
    assert body["reserved_bytes"] < _receipt(report)["manifest"]["total_bytes"]
    assert {item["role"] for item in body["artifacts"]} == {
        "pat_report",
        "pat_summary",
        "source_manifest",
    }

    session = app.state.quick_analysis_service.get_for_principal(
        body["analysis_session_id"], DEVELOPMENT_PRINCIPAL
    )
    assert session.status == QuickAnalysisStatus.SUCCESS
    assert session.source_manifest_mode == "LOCAL_PATH_SIZE_MTIME_V1"
    assert session.source_relative_path == "NCEAP020N10LL-520data"
    assert app.state.job_service.get(body["job_id"]).status == JobStatus.SUCCESS
    job_root = work_root / str(body["job_id"])
    assert (job_root / "PAT_001.xlsx").read_bytes() == report
    source_manifest = json.loads(
        (job_root / "source_manifest.json").read_text(encoding="utf-8")
    )
    assert source_manifest["mode"] == "LOCAL_PATH_SIZE_MTIME_V1"
    assert source_manifest["source_label"] == "NCEAP020N10LL-520data"
    assert not any((work_root / ".staging").iterdir())


def test_local_result_rejects_parameter_count_and_cleans_failed_upload(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    report = _pat_xlsx()
    response = TestClient(app).post(
        "/api/v1/quick-analysis/local-results",
        data={
            "receipt_json": json.dumps(
                _receipt(report, parameter_count=3), ensure_ascii=False
            )
        },
        files={"result_file": ("PAT_001.xlsx", report)},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == ("LOCAL_RESULT_PARAMETER_COUNT_MISMATCH")
    sessions = app.state.quick_analysis_service.list_for_principal(
        DEVELOPMENT_PRINCIPAL
    )
    assert len(sessions) == 1
    assert sessions[0].status == QuickAnalysisStatus.FAILED
    assert sessions[0].cleanup_status == "CLEANED"
    assert sessions[0].reserved_bytes == 0
    assert app.state.job_service.get(1).status == JobStatus.FAILED
    work_root = app.state.quick_capacity_policy.work_root
    assert not (work_root / "1").exists()
    assert not any((work_root / ".staging").iterdir())


def test_local_result_store_reaps_only_stale_uuid_staging_directories(
    tmp_path: Path,
) -> None:
    work_root = tmp_path / "quick-work"
    staging_root = work_root / ".staging"
    stale = staging_root / ("a" * 32)
    fresh = staging_root / ("b" * 32)
    unrelated = staging_root / "operator-note"
    for directory in (stale, fresh, unrelated):
        directory.mkdir(parents=True)
        (directory / "payload.tmp").write_text("temporary", encoding="utf-8")
    now = time.time()
    os.utime(stale, (now - 121, now - 121))
    os.utime(fresh, (now - 59, now - 59))
    os.utime(unrelated, (now - 121, now - 121))
    store = LocalQuickResultStore(work_root, staging_ttl_seconds=60)

    removed = store.reap_stale_staging(now_epoch_seconds=now)

    assert removed == ("a" * 32,)
    assert not stale.exists()
    assert fresh.is_dir()
    assert unrelated.is_dir()


def test_local_result_store_never_follows_links_while_reaping_staging(
    tmp_path: Path, monkeypatch
) -> None:
    work_root = tmp_path / "quick-work"
    stale = work_root / ".staging" / ("c" * 32)
    outside = tmp_path / "must-survive"
    stale.mkdir(parents=True)
    outside.mkdir()
    protected = outside / "raw-source.csv"
    protected.write_text("secret", encoding="utf-8")
    linked_outside = stale / "linked-outside"
    try:
        linked_outside.symlink_to(outside, target_is_directory=True)
    except OSError:
        linked_outside.mkdir()
        original_link_check = local_quick_result._is_link_or_reparse_point

        def simulated_reparse_point(path: Path) -> bool:
            return path == linked_outside or original_link_check(path)

        monkeypatch.setattr(
            local_quick_result,
            "_is_link_or_reparse_point",
            simulated_reparse_point,
        )
    now = time.time()
    os.utime(stale, (now - 121, now - 121))
    store = LocalQuickResultStore(work_root, staging_ttl_seconds=60)

    removed = store.reap_stale_staging(now_epoch_seconds=now)

    assert removed == ()
    assert stale.exists()
    assert protected.read_text(encoding="utf-8") == "secret"


def test_local_result_rejects_uploaded_bytes_that_do_not_match_receipt_sha(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    report = _pat_xlsx()
    receipt = _receipt(report)
    receipt["result"]["sha256"] = "d" * 64
    response = TestClient(app).post(
        "/api/v1/quick-analysis/local-results",
        data={"receipt_json": json.dumps(receipt)},
        files={"result_file": ("PAT_001.xlsx", report)},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "LOCAL_RESULT_SHA256_MISMATCH"
    session = app.state.quick_analysis_service.list_for_principal(
        DEVELOPMENT_PRINCIPAL
    )[0]
    assert session.status == QuickAnalysisStatus.FAILED
    assert session.cleanup_status == "CLEANED"
    assert session.reserved_bytes == 0
    assert app.state.job_service.get(1).status == JobStatus.FAILED
    work_root = app.state.quick_capacity_policy.work_root
    assert not (work_root / "1").exists()
    assert not any((work_root / ".staging").iterdir())


def test_local_result_rejects_formula_content_and_cleans_failed_upload(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    report = _pat_xlsx(formula=True)
    response = TestClient(app).post(
        "/api/v1/quick-analysis/local-results",
        data={"receipt_json": json.dumps(_receipt(report), ensure_ascii=False)},
        files={"result_file": ("PAT_001.xlsx", report)},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == ("LOCAL_RESULT_ACTIVE_CONTENT_FORBIDDEN")
    session = app.state.quick_analysis_service.list_for_principal(
        DEVELOPMENT_PRINCIPAL
    )[0]
    assert session.status == QuickAnalysisStatus.FAILED
    assert session.cleanup_status == "CLEANED"


def test_local_result_rejects_unapproved_xlsx_part_that_could_hide_raw_data(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    report = _pat_xlsx_with_hidden_raw_part()
    response = TestClient(app).post(
        "/api/v1/quick-analysis/local-results",
        data={"receipt_json": json.dumps(_receipt(report), ensure_ascii=False)},
        files={"result_file": ("PAT_001.xlsx", report)},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == ("LOCAL_RESULT_XLSX_PARTS_FORBIDDEN")
    session = app.state.quick_analysis_service.list_for_principal(
        DEVELOPMENT_PRINCIPAL
    )[0]
    assert session.status == QuickAnalysisStatus.FAILED
    assert session.cleanup_status == "CLEANED"
    work_root = app.state.quick_capacity_policy.work_root
    assert not (work_root / "1").exists()
    assert not any((work_root / ".staging").iterdir())


def test_local_result_receipt_is_strict_and_rejects_path_labels(tmp_path: Path) -> None:
    app = _app(tmp_path)
    report = _pat_xlsx()
    receipt = _receipt(report)
    receipt["source_label"] = r"F:\raw\520data"
    receipt["unexpected"] = "not allowed"
    response = TestClient(app).post(
        "/api/v1/quick-analysis/local-results",
        data={"receipt_json": json.dumps(receipt)},
        files={"result_file": ("PAT_001.xlsx", report)},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "LOCAL_RESULT_RECEIPT_INVALID"
    assert (
        app.state.quick_analysis_service.list_for_principal(DEVELOPMENT_PRINCIPAL) == ()
    )


def test_local_result_download_rechecks_registered_size_and_sha(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = _app(tmp_path)
    work_root = app.state.quick_capacity_policy.work_root
    monkeypatch.setenv("TMS_QUICK_WORK_ROOT", str(work_root))
    report = _pat_xlsx()
    client = TestClient(app)
    created = client.post(
        "/api/v1/quick-analysis/local-results",
        data={"receipt_json": json.dumps(_receipt(report), ensure_ascii=False)},
        files={"result_file": ("PAT_001.xlsx", report)},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    report_path = work_root / str(body["job_id"]) / "PAT_001.xlsx"
    report_path.write_bytes(b"x" * len(report))

    response = client.get(
        f"/api/v1/quick-analysis/sessions/{body['analysis_session_id']}/download"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == ("QUICK_RESULT_INTEGRITY_MISMATCH")


def test_system_admin_cannot_read_or_download_another_personal_quick_session(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    owner = Principal(
        10,
        "owner",
        "Owner",
        ("ANALYST",),
        frozenset({"ANALYSIS_RUN"}),
    )
    service = app.state.quick_analysis_service
    session = service.create(
        owner,
        NewQuickAnalysisSession(
            analysis_type="QUICK_PAT",
            test_stage="FT",
            factory_code="JIEQUN",
            source_root_code="LOCAL_AGENT",
            source_relative_path="private-source",
            source_manifest_mode="LOCAL_PATH_SIZE_MTIME_V1",
            source_manifest_json="{}",
            source_manifest_sha256="c" * 64,
            source_file_count=1,
            source_total_bytes=10,
            retention_mode="RESULT_ONLY",
            cleaner_release_id=21,
            expires_at_utc=datetime.now(UTC) + timedelta(days=7),
            access_scope="PERSONAL",
            data_domain_id=None,
        ),
    )
    service.attach_job(session.analysis_session_id, 7)
    report_path = tmp_path / "owner" / "PAT_001.xlsx"
    report_path.parent.mkdir()
    report_path.write_bytes(b"private")
    service.record_success(
        session.analysis_session_id,
        7,
        parameter_count=1,
        record_count=1,
        summary={},
        artifacts=(
            QuickAnalysisArtifact(
                "pat_report",
                str(report_path),
                report_path.stat().st_size,
                hashlib.sha256(report_path.read_bytes()).hexdigest(),
            ),
        ),
    )
    system_admin = Principal(
        1,
        "admin",
        "Admin",
        ("SYSTEM_ADMIN",),
        frozenset({"ANALYSIS_RUN"}),
    )
    app.dependency_overrides[current_principal] = lambda: system_admin
    client = TestClient(app)
    metadata = client.get(
        f"/api/v1/quick-analysis/sessions/{session.analysis_session_id}"
    )
    download = client.get(
        f"/api/v1/quick-analysis/sessions/{session.analysis_session_id}/download"
    )
    assert metadata.status_code == 404
    assert metadata.json()["error"]["code"] == "QUICK_ANALYSIS_NOT_FOUND"
    assert download.status_code == 404
    assert download.json()["error"]["code"] == "QUICK_ANALYSIS_NOT_FOUND"
