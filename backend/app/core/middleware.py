from __future__ import annotations

import logging
import os
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("tms.request")
_LOCAL_RESULT_PATH = "/api/v1/quick-analysis/local-results"
_DEFAULT_LOCAL_RESULT_HTTP_MAX_BYTES = 70 * 1024 * 1024


class _RequestBodyTooLarge(Exception):
    pass


class LocalResultBodyLimitMiddleware:
    """Bound Local result multipart bytes before Starlette spools UploadFile."""

    def __init__(self, app: ASGIApp, max_bytes: int | None = None) -> None:
        self.app = app
        configured = os.getenv("TMS_LOCAL_RESULT_HTTP_MAX_BYTES", "").strip()
        try:
            self.max_bytes = (
                int(configured)
                if max_bytes is None and configured
                else (
                    max_bytes
                    if max_bytes is not None
                    else _DEFAULT_LOCAL_RESULT_HTTP_MAX_BYTES
                )
            )
        except ValueError as exc:
            raise RuntimeError(
                "TMS_LOCAL_RESULT_HTTP_MAX_BYTES must be an integer"
            ) from exc
        if self.max_bytes < 1024:
            raise RuntimeError("TMS_LOCAL_RESULT_HTTP_MAX_BYTES must be at least 1024")

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or not (
            scope.get("method") == "POST" and scope.get("path") == _LOCAL_RESULT_PATH
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", ()))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_bytes = int(content_length)
            except ValueError:
                await self._reject(scope, receive, send, "请求体长度无效", 400)
                return
            if declared_bytes < 0:
                await self._reject(scope, receive, send, "请求体长度无效", 400)
                return
            if declared_bytes > self.max_bytes:
                await self._reject(scope, receive, send, "本机结果上传超过请求上限", 413)
                return

        received_bytes = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_bytes:
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            if response_started:
                raise
            await self._reject(scope, receive, send, "本机结果上传超过请求上限", 413)

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        message: str,
        status_code: int,
    ) -> None:
        response = JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": "LOCAL_RESULT_HTTP_BODY_TOO_LARGE"
                    if status_code == 413
                    else "LOCAL_RESULT_CONTENT_LENGTH_INVALID",
                    "message": message,
                    "details": [{"max_bytes": self.max_bytes}],
                    "request_id": None,
                }
            },
        )
        await response(scope, receive, send)


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
