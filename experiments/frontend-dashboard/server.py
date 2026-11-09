"""C1 浏览器版中控试验：静态托管 + daemon 反向代理（仅本地开发）。

只复用项目既有依赖（FastAPI / uvicorn / httpx），不新增 Python 依赖。
Bearer token 由本代理从 ``$SENTINEL_ORCH_HOME/token`` 读取并注入，前端不可见。
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOME = PROJECT_ROOT / ".claude" / "orchestrator"
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_TIMEOUT = 10.0


def orch_home() -> Path:
    raw = os.environ.get("SENTINEL_ORCH_HOME", "").strip()
    return Path(raw).resolve() if raw else DEFAULT_HOME


def load_token(home: Path) -> str:
    try:
        return (home / "token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def daemon_base(home: Path) -> str | None:
    override = os.environ.get("SENTINEL_ORCH_URL", "").strip()
    if override:
        return override.rstrip("/")
    try:
        info = json.loads((home / "daemon.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    host = info.get("host")
    port = info.get("port")
    if not isinstance(host, str) or not isinstance(port, int):
        return None
    return f"http://{host}:{port}"


def _down() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "中控未启动",
            "hint": "请先运行 scripts\\start_orchestrator.cmd 或 -Action start",
        },
    )


def _headers(home: Path) -> dict[str, str]:
    token = load_token(home)
    return {"Authorization": f"Bearer {token}"} if token else {}


def create_app() -> FastAPI:
    app = FastAPI(title="AgentSentinel C1 Dashboard (experiment)", docs_url=None, redoc_url=None)

    @app.get("/api/events")
    async def events(request: Request) -> Response:
        del request
        home = orch_home()
        base = daemon_base(home)
        if base is None:
            return _down()
        client = httpx.AsyncClient(timeout=None, trust_env=False)
        upstream_request = client.build_request("GET", f"{base}/events", headers=_headers(home))
        try:
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError:
            await client.aclose()
            return _down()
        if upstream.status_code != 200:
            body = await upstream.aread()
            await upstream.aclose()
            await client.aclose()
            return Response(
                content=body,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type", "application/json"),
            )

        async def passthrough() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            except httpx.HTTPError as exc:
                payload = json.dumps({"error": "daemon disconnected", "detail": str(exc)})
                yield f"event: error\ndata: {payload}\n\n".encode()
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            passthrough(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy(path: str, request: Request) -> Response:
        home = orch_home()
        base = daemon_base(home)
        if base is None:
            return _down()
        query = request.url.query
        url = f"{base}/{path}" + (f"?{query}" if query else "")
        headers = _headers(home)
        content_type = request.headers.get("content-type")
        if content_type:
            headers["Content-Type"] = content_type
        body = await request.body()
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, trust_env=False) as client:
                upstream = await client.request(
                    request.method,
                    url,
                    headers=headers,
                    content=body or None,
                )
        except httpx.HTTPError:
            return _down()
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="c1-dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    print(f"C1 dashboard: http://{args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
