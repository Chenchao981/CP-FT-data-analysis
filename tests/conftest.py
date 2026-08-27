from __future__ import annotations

import pytest
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _explicit_test_authentication_mode(monkeypatch):
    """Unit/API tests deliberately use the authentication-disabled test mode."""
    monkeypatch.setenv("TMS_ENV", "test")
    monkeypatch.setenv("TMS_AUTH_REQUIRED", "false")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()
