from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.core.errors import DomainError
from app.domain.auth import DEVELOPMENT_PRINCIPAL
from app.domain.cleaner_registry import CleanerRelease
from app.domain.jobs import Job, JobStatus, JobType, TriggerType
from app.domain.quick_analysis import (
    InMemoryQuickAnalysisService,
    NewQuickAnalysisSession,
    QuickAnalysisArtifact,
    QuickAnalysisStatus,
)
from app.infrastructure.direct_path_source import build_direct_path_manifest
from app.infrastructure.quick_pat_runner import QuickPatRunResult
from app.infrastructure.source_catalog import SourceCatalog, SourceRoot
from app.workers.route_a_worker import QuickPatHandler


class StubRegistry:
    def __init__(self, release: CleanerRelease) -> None:
        self.release = release

    def get_released(self, release_id: int) -> CleanerRelease:
        assert release_id == self.release.cleaner_release_id
        return self.release


class StubRunner:
    def __init__(
        self,
        report: Path,
        mutate_source: Path | None = None,
        on_run=None,
    ) -> None:
        self.report = report
        self.mutate_source = mutate_source
        self.on_run = on_run
        self.calls = 0

    def run_release(self, **kwargs) -> QuickPatRunResult:
        self.calls += 1
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.report.write_bytes(b"pat")
        if self.mutate_source is not None:
            (self.mutate_source / "changed.csv").write_text("x\n2\n", encoding="utf-8")
        if self.on_run is not None:
            self.on_run()
        artifact = QuickAnalysisArtifact(
            "pat_report",
            str(self.report),
            self.report.stat().st_size,
            hashlib.sha256(self.report.read_bytes()).hexdigest(),
        )
        return QuickPatRunResult(
            23,
            6_813_800,
            {"parameter_count": 23, "record_count": 6_813_800},
            (artifact,),
            "ok",
        )


def _scenario(tmp_path: Path):
    root = tmp_path / "shared"
    product = root / "product-a"
    product.mkdir(parents=True)
    (product / "one.csv").write_text("x\n1\n", encoding="utf-8")
    catalog = SourceCatalog(
        (
            SourceRoot(
                "ROOT",
                "Root",
                root,
                "FT",
                "JIEQUN",
                (".csv",),
                data_domain_code="JIEQUN_FT",
            ),
        )
    )
    manifest = catalog.build_manifest("ROOT", "product-a")
    grants = {(DEVELOPMENT_PRINCIPAL.user_id, 7)}
    service = InMemoryQuickAnalysisService(
        domain_grant_checker=lambda user_id, domain_id: (user_id, domain_id) in grants
    )
    session = service.create(
        DEVELOPMENT_PRINCIPAL,
        NewQuickAnalysisSession(
            "QUICK_PAT",
            "FT",
            "JIEQUN",
            "ROOT",
            "product-a",
            manifest.mode,
            manifest.as_json(),
            manifest.sha256,
            manifest.file_count,
            manifest.total_bytes,
            "RESULT_ONLY",
            21,
            datetime.now(UTC) + timedelta(days=7),
            "DOMAIN",
            7,
            "JIEQUN_FT",
        ),
    )
    service.attach_job(session.analysis_session_id, 44)
    package = tmp_path / "ft.pyz"
    runtime = tmp_path / "python.exe"
    package.write_bytes(b"package")
    runtime.touch()
    release = CleanerRelease(
        21,
        8,
        "FT",
        "JIEQUN",
        "FORMAT",
        "v1",
        "PAT",
        "v1",
        hashlib.sha256(package.read_bytes()).hexdigest(),
        str(package),
        str(runtime),
        "entrypoint",
        "JIEQUN_FT_QUICK_PAT_PYZ",
        "JIEQUN_UNIFIED_CSV_DIRECTORY_V1",
        "FT_PAT_RESULT_V1",
        None,
        3600,
        10_000_000,
    )
    now = datetime.now(UTC)
    job = Job(
        44,
        None,
        None,
        session.analysis_session_id,
        21,
        JobType.QUICK_PAT,
        TriggerType.MANUAL,
        "tester",
        1,
        None,
        JobStatus.RUNNING,
        now,
        started_at_utc=now,
        lease_token="11111111-1111-1111-1111-111111111111",
        attempt_count=1,
    )
    report = tmp_path / "work" / "PAT_001" / "PAT_001.xlsx"
    return product, catalog, service, session, release, job, report, grants


def test_quick_pat_handler_checks_manifest_and_records_result(tmp_path: Path) -> None:
    _product, catalog, service, session, release, job, report, _grants = _scenario(
        tmp_path
    )
    QuickPatHandler(
        StubRegistry(release),
        service,
        catalog,
        runner=StubRunner(report),
        work_root=tmp_path / "work",
    )(job)

    completed = service.get_for_principal(
        session.analysis_session_id, DEVELOPMENT_PRINCIPAL
    )
    assert completed.status.value == "SUCCESS"
    assert completed.parameter_count == 23
    assert completed.record_count == 6_813_800


def test_quick_pat_handler_runs_personal_direct_path(tmp_path: Path) -> None:
    source = tmp_path / "local" / "520data"
    source.mkdir(parents=True)
    (source / "one.csv").write_text("x\n1\n", encoding="utf-8")
    resolved, manifest = build_direct_path_manifest(source)
    service = InMemoryQuickAnalysisService()
    session = service.create(
        DEVELOPMENT_PRINCIPAL,
        NewQuickAnalysisSession(
            "QUICK_PAT",
            "FT",
            "JIEQUN",
            "LOCAL_AGENT",
            str(resolved),
            manifest.mode,
            manifest.as_json(),
            manifest.sha256,
            manifest.file_count,
            manifest.total_bytes,
            "RESULT_ONLY",
            21,
            datetime.now(UTC) + timedelta(days=7),
            "PERSONAL",
            None,
        ),
    )
    service.attach_job(session.analysis_session_id, 45)
    package = tmp_path / "ft.pyz"
    runtime = tmp_path / "python.exe"
    package.write_bytes(b"package")
    runtime.touch()
    release = CleanerRelease(
        21, 8, "FT", "JIEQUN", "FORMAT", "v1", "PAT", "v1",
        hashlib.sha256(package.read_bytes()).hexdigest(), str(package), str(runtime),
        "entrypoint", "JIEQUN_FT_QUICK_PAT_PYZ",
        "JIEQUN_UNIFIED_CSV_DIRECTORY_V1", "FT_PAT_RESULT_V1", None, 3600,
        10_000_000,
    )
    now = datetime.now(UTC)
    job = Job(
        45, None, None, session.analysis_session_id, 21, JobType.QUICK_PAT,
        TriggerType.MANUAL, "tester", 1, None, JobStatus.RUNNING, now,
        started_at_utc=now,
    )
    report = tmp_path / "work" / "PAT_001.xlsx"

    QuickPatHandler(
        StubRegistry(release), service, SourceCatalog(), runner=StubRunner(report),
        work_root=tmp_path / "work",
    )(job)

    completed = service.get_for_principal(
        session.analysis_session_id, DEVELOPMENT_PRINCIPAL
    )
    assert completed.status == QuickAnalysisStatus.SUCCESS
    assert completed.access_scope == "PERSONAL"
    assert completed.record_count == 6_813_800


def test_quick_pat_handler_rejects_source_change_during_calculation(
    tmp_path: Path,
) -> None:
    product, catalog, service, session, release, job, report, _grants = _scenario(
        tmp_path
    )
    with pytest.raises(RuntimeError, match="changed while"):
        QuickPatHandler(
            StubRegistry(release),
            service,
            catalog,
            runner=StubRunner(report, mutate_source=product),
            work_root=tmp_path / "work",
        )(job)
    failed = service.get_for_principal(
        session.analysis_session_id, DEVELOPMENT_PRINCIPAL
    )
    assert failed.status == QuickAnalysisStatus.FAILED
    assert failed.error_code == "QUICK_PAT_FAILED"


def test_quick_pat_handler_rechecks_grant_before_runner_starts(tmp_path: Path) -> None:
    (
        _product,
        catalog,
        service,
        _session,
        release,
        job,
        report,
        grants,
    ) = _scenario(tmp_path)
    grants.clear()
    runner = StubRunner(report)

    with pytest.raises(DomainError) as captured:
        QuickPatHandler(
            StubRegistry(release),
            service,
            catalog,
            runner=runner,
            work_root=tmp_path / "work",
        )(job)

    assert captured.value.code == "QUICK_DATA_DOMAIN_ACCESS_REVOKED"
    assert runner.calls == 0


def test_quick_pat_handler_rechecks_grant_before_success(tmp_path: Path) -> None:
    (
        _product,
        catalog,
        service,
        _session,
        release,
        job,
        report,
        grants,
    ) = _scenario(tmp_path)
    runner = StubRunner(report, on_run=grants.clear)

    with pytest.raises(DomainError) as captured:
        QuickPatHandler(
            StubRegistry(release),
            service,
            catalog,
            runner=runner,
            work_root=tmp_path / "work",
        )(job)

    assert captured.value.code == "QUICK_DATA_DOMAIN_ACCESS_REVOKED"
    assert runner.calls == 1
