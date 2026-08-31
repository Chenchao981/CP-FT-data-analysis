from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("tms.request")


def _analytics_group(path: str) -> str | None:
    if path == "/api/v1/analytics/features":
        return None
    if path in {"/api/v1/analytics/overview", "/api/v1/analytics/instant-risk"}:
        return "OVERVIEW"
    if path in {"/api/v1/analytics/detail", "/api/v1/analytics/drilldown"}:
        return "DETAIL"
    if path == "/api/v1/datasets/parameter-analysis" or path == "/api/v1/analytics/parameter-relationship":
        return "PARAMETER"
    if path.startswith("/api/v1/analytics/spatial"):
        return "SPATIAL"
    if path.startswith("/api/v1/analytics/quality-evaluation"):
        return "QUALITY"
    if path.startswith(
        (
            "/api/v1/analytics/saved-analyses",
            "/api/v1/analytics/exports",
            "/api/v1/analytics/wafer-summary",
        )
    ):
        return "DELIVERY"
    return None


class AnalyticsFeatureFlagMiddleware(BaseHTTPMiddleware):
    """Backend kill switches; direct URLs cannot bypass disabled groups."""

    async def dispatch(self, request: Request, call_next):
        group = _analytics_group(request.url.path)
        flags = getattr(request.app.state, "analytics_feature_flags", {})
        if group is not None and flags.get(group) is False:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "ANALYSIS_FEATURE_DISABLED",
                        "message": f"{group} analytics is disabled by the release kill switch",
                        "details": [{"feature_group": group}],
                        "request_id": getattr(request.state, "request_id", None),
                    }
                },
            )
        return await call_next(request)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%.2f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
            request_id,
        )
        return response
