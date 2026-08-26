from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.quick_analysis import (
    InMemoryQuickAnalysisService,
    NewQuickAnalysisSession,
    QuickAnalysisArtifact,
    QuickAnalysisStatus,
)


def _principal(user_id: int, name: str) -> Principal:
    return Principal(user_id, name, name, ("ANALYST",), frozenset({"ANALYSIS_RUN"}))


def _request(expires_at: datetime) -> NewQuickAnalysisSession:
    return NewQuickAnalysisSession(
        "QUICK_PAT",
        "FT",
        "JIEQUN",
        "ROOT",
        "product",
        "PATH_SIZE_MTIME_V1",
        '{"file_count":1}',
        "a" * 64,
        1,
        10,
        "RESULT_ONLY",
        21,
        expires_at,
    )


def test_non_admin_cannot_read_another_users_quick_session() -> None:
    service = InMemoryQuickAnalysisService()
    alice = _principal(10, "alice")
    bob = _principal(11, "bob")
    session = service.create(alice, _request(datetime.now(UTC) + timedelta(days=7)))
    with pytest.raises(DomainError) as captured:
        service.get_for_principal(session.analysis_session_id, bob)
    assert captured.value.code == "QUICK_ANALYSIS_NOT_FOUND"


def test_expired_result_is_not_downloadable(tmp_path: Path) -> None:
    service = InMemoryQuickAnalysisService()
    alice = _principal(10, "alice")
    session = service.create(alice, _request(datetime.now(UTC) - timedelta(seconds=1)))
    service.attach_job(session.analysis_session_id, 5)
    report = tmp_path / "PAT.xlsx"
    report.write_bytes(b"pat")
    service.record_success(
        session.analysis_session_id,
        5,
        parameter_count=1,
        record_count=1,
        summary={},
        artifacts=(
            QuickAnalysisArtifact(
                "pat_report",
                str(report),
                report.stat().st_size,
                hashlib.sha256(report.read_bytes()).hexdigest(),
            ),
        ),
    )
    assert (
        service.get_for_principal(session.analysis_session_id, alice).status
        == QuickAnalysisStatus.EXPIRED
    )
    with pytest.raises(DomainError) as captured:
        service.result_artifact(session.analysis_session_id, alice)
    assert captured.value.code == "QUICK_RESULT_EXPIRED"
