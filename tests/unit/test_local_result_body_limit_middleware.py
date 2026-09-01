from __future__ import annotations

import asyncio

from app.core.middleware import LocalResultBodyLimitMiddleware
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


def _app(limit: int = 1024) -> FastAPI:
    app = FastAPI()
    app.add_middleware(LocalResultBodyLimitMiddleware, max_bytes=limit)

    @app.post("/api/v1/quick-analysis/local-results")
    async def local_result(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    @app.post("/unrelated")
    async def unrelated(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    return app


def test_rejects_declared_oversize_before_route_body_parsing() -> None:
    response = TestClient(_app()).post(
        "/api/v1/quick-analysis/local-results",
        content=b"x" * 2048,
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "LOCAL_RESULT_HTTP_BODY_TOO_LARGE"


def test_does_not_apply_local_result_limit_to_other_routes() -> None:
    response = TestClient(_app()).post("/unrelated", content=b"x" * 2048)
    assert response.status_code == 200
    assert response.json() == {"bytes": 2048}


def test_rejects_chunked_body_when_actual_bytes_cross_limit() -> None:
    received = iter(
        (
            {"type": "http.request", "body": b"x" * 700, "more_body": True},
            {"type": "http.request", "body": b"y" * 700, "more_body": False},
        )
    )
    sent: list[dict] = []

    async def downstream(scope, receive, send) -> None:
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive() -> dict:
        return next(received)

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/quick-analysis/local-results",
        "raw_path": b"/api/v1/quick-analysis/local-results",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    asyncio.run(
        LocalResultBodyLimitMiddleware(downstream, max_bytes=1024)(
            scope, receive, send
        )
    )
    assert sent[0]["status"] == 413
