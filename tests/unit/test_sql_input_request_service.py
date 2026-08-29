from __future__ import annotations

import inspect

from app.infrastructure.sql_input_request_service import (
    SqlProcessingInputRequestService,
)


def test_lot_input_resume_creates_atomic_initial_import_job() -> None:
    source = inspect.getsource(SqlProcessingInputRequestService.resolve)

    assert "max_attempts,finalize_protocol" in source
    assert "'ATOMIC_V1'" in source
