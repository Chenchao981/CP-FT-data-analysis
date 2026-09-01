from __future__ import annotations

import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from .config import AgentConfig
from .errors import AgentError
from .models import RunRequest, SelectFolderRequest, ToolRequest
from .service import FolderSelector, LocalAgentService, ToolRunner

AGENT_VERSION = "0.1.0"
TOKEN_HEADER = "X-TMS-Agent-Token"
ALLOWED_METHODS = {"GET", "POST", "DELETE", "OPTIONS"}
ALLOWED_REQUEST_HEADERS = {"content-type", TOKEN_HEADER.lower()}


class LoopbackSecurityMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: FastAPI,
        *,
        allowed_hosts: tuple[str, ...],
        allowed_origins: tuple[str, ...],
        pairing_token: str,
        pairing_token_ttl_seconds: int,
    ) -> None:
        super().__init__(app)
        self._allowed_hosts = {item.lower() for item in allowed_hosts}
        self._allowed_origins = set(allowed_origins)
        self._pairing_token = pairing_token
        self._pairing_token_expires_at = (
            time.monotonic() + pairing_token_ttl_seconds
        )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        host = request.headers.get("host", "").lower()
        if host not in self._allowed_hosts:
            return _error_response(
                "LOCAL_HOST_REJECTED", "Local Agent Host 请求不在允许范围内", 400
            )
        origin = request.headers.get("origin")
        if origin is not None and origin not in self._allowed_origins:
            return _error_response(
                "LOCAL_ORIGIN_REJECTED", "页面来源不允许访问 Local Agent", 403
            )
        if request.method == "OPTIONS":
            return self._preflight(request, origin)
        if request.url.path != "/v1/health":
            if origin is None:
                return _error_response(
                    "LOCAL_ORIGIN_REQUIRED", "访问 Local Agent 必须携带页面来源", 403
                )
            supplied = request.headers.get(TOKEN_HEADER, "")
            if time.monotonic() >= self._pairing_token_expires_at:
                response = _error_response(
                    "LOCAL_TOKEN_EXPIRED",
                    "Local Agent 配对令牌已过期，请重启 Agent 后重新配对",
                    401,
                )
                return _with_cors(response, origin)
            if not supplied or not secrets.compare_digest(
                supplied, self._pairing_token
            ):
                response = _error_response(
                    "LOCAL_TOKEN_REJECTED", "Local Agent 配对令牌无效", 401
                )
                return _with_cors(response, origin)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if origin is not None:
            _with_cors(response, origin)
        return response

    def _preflight(self, request: Request, origin: str | None) -> Response:
        if origin is None:
            return _error_response(
                "LOCAL_ORIGIN_REQUIRED", "跨域预检必须携带页面来源", 403
            )
        requested_method = request.headers.get(
            "access-control-request-method", ""
        ).upper()
        if requested_method not in ALLOWED_METHODS - {"OPTIONS"}:
            response = _error_response(
                "LOCAL_METHOD_REJECTED", "跨域预检方法不允许", 405
            )
            return _with_cors(response, origin)
        raw_headers = request.headers.get("access-control-request-headers", "")
        requested_headers = {
            item.strip().lower() for item in raw_headers.split(",") if item.strip()
        }
        if not requested_headers.issubset(ALLOWED_REQUEST_HEADERS):
            response = _error_response(
                "LOCAL_HEADER_REJECTED", "跨域预检请求头不允许", 403
            )
            return _with_cors(response, origin)
        response = Response(status_code=204)
        _with_cors(response, origin)
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-TMS-Agent-Token"
        )
        response.headers["Access-Control-Max-Age"] = "600"
        if request.headers.get("access-control-request-private-network") == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def create_app(
    config: AgentConfig | None = None,
    *,
    pairing_token: str | None = None,
    selector: FolderSelector | None = None,
    runner: ToolRunner | None = None,
) -> FastAPI:
    agent_config = config or AgentConfig.defaults()
    agent_config.validate()
    token = pairing_token or secrets.token_urlsafe(32)
    if len(token) < 32:
        raise RuntimeError("Local Agent pairing token must be at least 32 characters")
    service = LocalAgentService(
        agent_config,
        selector=selector,
        runner=runner,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            service.close()

    app = FastAPI(
        title="TMS Local Agent",
        version=AGENT_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.local_agent_service = service
    app.state.pairing_token = token
    app.state.agent_config = agent_config
    app.add_middleware(
        LoopbackSecurityMiddleware,
        allowed_hosts=agent_config.resolved_allowed_hosts(),
        allowed_origins=agent_config.allowed_origins,
        pairing_token=token,
        pairing_token_ttl_seconds=agent_config.pairing_token_ttl_seconds,
    )

    @app.exception_handler(AgentError)
    async def agent_error_handler(_: Request, exc: AgentError) -> JSONResponse:
        return _error_response(exc.code, exc.message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request, __: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's default response echoes invalid input, which could reveal a path.
        return _error_response(
            "LOCAL_REQUEST_INVALID", "Local Agent 请求格式不符合接口合同", 422
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, __: Exception) -> JSONResponse:
        return _error_response(
            "LOCAL_AGENT_ERROR", "Local Agent 发生未预期错误，请检查本机日志", 500
        )

    @app.get("/v1/health")
    def health() -> dict[str, object]:
        return {
            "service": "tms-local-agent",
            "version": AGENT_VERSION,
            "status": "ok",
            "bind_scope": "loopback-only",
            "pairing_required": True,
            "token_required": True,
            "pairing_token_ttl_seconds": agent_config.pairing_token_ttl_seconds,
        }

    @app.get("/v1/tools")
    def tools() -> dict[str, object]:
        return {"tools": service.list_tools()}

    @app.post("/v1/select-folder")
    def select_folder(_: SelectFolderRequest) -> dict[str, str]:
        return service.select_folder()

    @app.post("/v1/selections/{selection_id}/preview")
    def preview(selection_id: str, body: ToolRequest) -> dict[str, object]:
        return service.preview(selection_id, body.tool_code)

    @app.post("/v1/selections/{selection_id}/runs")
    def create_run(selection_id: str, body: RunRequest) -> dict[str, str]:
        return service.create_run(
            selection_id,
            body.tool_code,
            body.confirmed_manifest_sha256,
        )

    @app.get("/v1/runs/{run_id}")
    def run_status(run_id: str) -> dict[str, object]:
        return service.get_status(run_id)

    @app.get("/v1/runs/{run_id}/receipt")
    def run_receipt(run_id: str) -> dict[str, object]:
        return service.get_receipt(run_id)

    @app.get("/v1/runs/{run_id}/result")
    def run_result(run_id: str) -> FileResponse:
        path, filename = service.get_result(run_id)
        return FileResponse(
            path,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            filename=filename,
        )

    @app.delete("/v1/runs/{run_id}", status_code=204)
    def delete_run(run_id: str) -> Response:
        service.delete_run(run_id)
        return Response(status_code=204)

    return app


def _error_response(code: str, message: str, status_code: int) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _with_cors(response: Response, origin: str) -> Response:
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
    return response
