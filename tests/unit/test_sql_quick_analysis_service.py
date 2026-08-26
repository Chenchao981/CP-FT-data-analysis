from __future__ import annotations

from datetime import UTC, datetime

from app.domain.quick_analysis import QuickAnalysisStatus
from app.infrastructure.sql_quick_analysis_service import _to_session


def test_database_quick_session_row_maps_summary_and_effective_status() -> None:
    row = {
        "analysis_session_id": 7,
        "owner_user_id": 2,
        "owner_login": "analyst",
        "owner_name": "分析员",
        "analysis_type": "QUICK_PAT",
        "test_stage": "FT",
        "factory_code": "JIEQUN",
        "source_root_code": "JIEQUN_SHARED",
        "source_relative_path": "520data",
        "source_manifest_mode": "PATH_SIZE_MTIME_V1",
        "source_manifest_sha256": "a" * 64,
        "source_file_count": 520,
        "source_total_bytes": 3_041_085_645,
        "retention_mode": "RESULT_ONLY",
        "cleaner_release_id": 21,
        "effective_status": "SUCCESS",
        "job_id": 40,
        "job_status": "SUCCESS",
        "parameter_count": 23,
        "record_count": 6_813_800,
        "summary_json": '{"elapsed_seconds":153.906}',
        "result_file_name": "PAT_001.xlsx",
        "result_size_bytes": 7759,
        "effective_error_code": None,
        "effective_error_message": None,
        "expires_at_utc": datetime(2026, 9, 2, 1, 0, tzinfo=UTC),
        "created_at_utc": datetime(2026, 8, 26, 1, 0, tzinfo=UTC),
        "started_at_utc": datetime(2026, 8, 26, 1, 1, tzinfo=UTC),
        "finished_at_utc": datetime(2026, 8, 26, 1, 4, tzinfo=UTC),
    }
    session = _to_session(row)
    assert session.status == QuickAnalysisStatus.SUCCESS
    assert session.summary == {"elapsed_seconds": 153.906}
    assert session.record_count == 6_813_800
    assert session.created_at_utc.tzinfo == UTC
