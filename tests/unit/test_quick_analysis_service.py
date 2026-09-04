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
        "LOCAL_AGENT",
        "product",
        "LOCAL_PATH_SIZE_MTIME_V1",
        '{"file_count":1}',
        "a" * 64,
        1,
        10,
        "RESULT_ONLY",
        21,
        expires_at,
        "PERSONAL",
        None,
    )


def _domain_request(expires_at: datetime) -> NewQuickAnalysisSession:
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
        "DOMAIN",
        7,
        "JIEQUN_FT",
    )


def test_non_admin_cannot_read_another_users_quick_session() -> None:
    service = InMemoryQuickAnalysisService()
    alice = _principal(10, "alice")
    bob = _principal(11, "bob")
    session = service.create(alice, _request(datetime.now(UTC) + timedelta(days=7)))
    with pytest.raises(DomainError) as captured:
        service.get_for_principal(session.analysis_session_id, bob)
    assert captured.value.code == "QUICK_ANALYSIS_NOT_FOUND"


def test_domain_members_can_read_but_nonmembers_and_system_admin_cannot() -> None:
    grants = {(10, 7), (11, 7)}
    service = InMemoryQuickAnalysisService(
        domain_grant_checker=lambda user_id, domain_id: (user_id, domain_id) in grants
    )
    owner = _principal(10, "owner")
    member = _principal(11, "member")
    outsider = _principal(12, "outsider")
    admin = Principal(
        99,
        "admin",
        "Admin",
        ("SYSTEM_ADMIN",),
        frozenset({"ANALYSIS_RUN"}),
    )
    session = service.create(
        owner, _domain_request(datetime.now(UTC) + timedelta(days=7))
    )

    assert service.get_for_principal(session.analysis_session_id, member) == session
    assert service.list_for_principal(member, access_scope="DOMAIN") == (session,)
    for denied in (outsider, admin):
        with pytest.raises(DomainError) as captured:
            service.get_for_principal(session.analysis_session_id, denied)
        assert captured.value.code == "QUICK_ANALYSIS_NOT_FOUND"


def test_revoked_or_expired_domain_grant_fails_closed_at_start_and_read() -> None:
    grants = {(10, 7), (11, 7)}
    service = InMemoryQuickAnalysisService(
        domain_grant_checker=lambda user_id, domain_id: (user_id, domain_id) in grants
    )
    owner = _principal(10, "owner")
    member = _principal(11, "member")
    session = service.create(
        owner, _domain_request(datetime.now(UTC) + timedelta(days=7))
    )
    grants.remove((10, 7))
    with pytest.raises(DomainError) as start_error:
        service.mark_running(session.analysis_session_id)
    assert start_error.value.code == "QUICK_DATA_DOMAIN_ACCESS_REVOKED"

    grants.remove((11, 7))
    with pytest.raises(DomainError) as read_error:
        service.get_for_principal(session.analysis_session_id, member)
    assert read_error.value.code == "QUICK_ANALYSIS_NOT_FOUND"


def test_domain_grant_is_checked_again_before_success_is_recorded(
    tmp_path: Path,
) -> None:
    grants = {(10, 7)}
    service = InMemoryQuickAnalysisService(
        domain_grant_checker=lambda user_id, domain_id: (user_id, domain_id) in grants
    )
    owner = _principal(10, "owner")
    session = service.create(
        owner, _domain_request(datetime.now(UTC) + timedelta(days=7))
    )
    service.attach_job(session.analysis_session_id, 5)
    service.mark_running(session.analysis_session_id)
    report = tmp_path / "PAT.xlsx"
    report.write_bytes(b"pat")
    grants.clear()

    with pytest.raises(DomainError) as captured:
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
    assert captured.value.code == "QUICK_DATA_DOMAIN_ACCESS_REVOKED"


def test_successful_result_remains_in_personal_history_after_session_deadline(
    tmp_path: Path,
) -> None:
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
        == QuickAnalysisStatus.SUCCESS
    )
    assert service.result_artifact(session.analysis_session_id, alice).path == str(report)
