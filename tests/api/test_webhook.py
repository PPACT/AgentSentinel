"""P1-1 Webhook 路由：验签 401、分类 202 与分发调用。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import Callable, Coroutine
from typing import Any, cast

from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.types import Receive, Scope

from agentsentinel.api.webhook import SIGNATURE_HEADER, create_app

# secret-fixture: 分段拼接的测试值，避免完整密钥形态落盘
SIGNING_VALUE = "test" + "-secret"
ENDPOINT_PATH = "/api/webhook/{source}"


def _body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _sign(body: bytes, secret: str = SIGNING_VALUE) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _request(body: bytes, signature: str | None) -> Request:
    headers = [(SIGNATURE_HEADER.lower().encode(), signature.encode())] if signature else []
    message: dict[str, Any] = {"type": "http.request", "body": body, "more_body": False}

    async def receive() -> dict[str, Any]:
        return message

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/webhook/slack",
        "raw_path": b"/api/webhook/slack",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("testclient", 123),
        "server": ("testserver", 80),
    }
    return Request(cast(Scope, scope), receive=cast(Receive, receive))


def _endpoint(app: FastAPI) -> Callable[..., Coroutine[Any, Any, JSONResponse]]:
    route: Any = next(
        route for route in app.routes if getattr(route, "path", None) == ENDPOINT_PATH
    )
    return cast(Callable[..., Coroutine[Any, Any, JSONResponse]], route.endpoint)


def _json(response: JSONResponse) -> dict[str, str]:
    return cast(dict[str, str], json.loads(bytes(response.body)))


def _call(
    app: FastAPI,
    body: bytes,
    signature: str | None,
    background_tasks: BackgroundTasks | None = None,
) -> JSONResponse:
    return asyncio.run(
        _endpoint(app)(
            source="slack",
            request=_request(body, signature),
            background_tasks=background_tasks or BackgroundTasks(),
        )
    )


def test_bad_signature_returns_401_without_dispatch() -> None:
    commands: list[tuple[str, str]] = []
    app = create_app(secret=SIGNING_VALUE, command_handler=lambda c, a: commands.append((c, a)))
    body = _body({"text": "/run-test smoke"})

    response = _call(app, body, "sha256=" + "0" * 64)

    assert response.status_code == 401
    assert commands == []


def test_drop_returns_202_without_handler() -> None:
    commands: list[tuple[str, str]] = []
    tasks = BackgroundTasks()
    app = create_app(secret=SIGNING_VALUE, command_handler=lambda c, a: commands.append((c, a)))
    body = _body({"text": "普通聊天"})

    response = _call(app, body, _sign(body), tasks)

    assert response.status_code == 202
    assert _json(response) == {"decision": "DROP"}
    assert commands == []
    assert tasks.tasks == []


def test_fast_path_calls_command_handler() -> None:
    commands: list[tuple[str, str]] = []
    app = create_app(secret=SIGNING_VALUE, command_handler=lambda c, a: commands.append((c, a)))
    body = _body({"text": "/run-test smoke"})

    response = _call(app, body, _sign(body))

    assert response.status_code == 202
    assert _json(response) == {"decision": "FAST_PATH"}
    assert commands == [("run-test", "smoke")]


def test_slow_path_schedules_pipeline_in_background() -> None:
    runs: list[str] = []
    tasks = BackgroundTasks()
    app = create_app(secret=SIGNING_VALUE, pipeline_runner=runs.append)
    body = _body({"text": "@AgentSentinel 重构鉴权"})

    response = _call(app, body, _sign(body), tasks)

    assert response.status_code == 202
    assert _json(response) == {"decision": "SLOW_PATH"}
    assert runs == []  # 请求线程内不执行流水线
    assert len(tasks.tasks) == 1
    asyncio.run(tasks())
    assert runs == ["重构鉴权"]


def test_slow_path_uses_payload_labels() -> None:
    runs: list[str] = []
    tasks = BackgroundTasks()
    app = create_app(secret=SIGNING_VALUE, pipeline_runner=runs.append)
    body = _body({"text": "登录失败", "labels": ["Bug"]})

    response = _call(app, body, _sign(body), tasks)

    assert response.status_code == 202
    asyncio.run(tasks())
    assert runs == ["登录失败"]


def test_invalid_payload_returns_400() -> None:
    app = create_app(secret=SIGNING_VALUE)
    body = _body({"labels": ["bug"]})

    response = _call(app, body, _sign(body))

    assert response.status_code == 400
