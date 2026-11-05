"""P1-1 Webhook 路由：验签 401、分类 202 与分发调用。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from starlette.requests import Request
from starlette.types import Receive, Scope

from agentsentinel.api.webhook import (
    SIGNATURE_HEADER,
    create_app,
    create_app_from_settings,
)
from agentsentinel.config import Settings
from agentsentinel.workspace import Workspace

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
    source: str = "slack",
) -> JSONResponse:
    return asyncio.run(
        _endpoint(app)(
            source=source,
            request=_request(body, signature),
            background_tasks=background_tasks or BackgroundTasks(),
        )
    )


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure()
    workspace.write_text("code/main.py", "def main() -> None:\n    pass\n")
    return workspace


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


def test_github_source_uses_adapter() -> None:
    runs: list[str] = []
    tasks = BackgroundTasks()
    app = create_app(secret=SIGNING_VALUE, pipeline_runner=runs.append)
    body = _body({"issue": {"title": "@AgentSentinel 重构鉴权"}})

    response = _call(app, body, _sign(body), tasks, source="github")

    assert response.status_code == 202
    assert _json(response) == {"decision": "SLOW_PATH"}
    assert len(tasks.tasks) == 1
    asyncio.run(tasks())
    assert runs == ["重构鉴权"]


def test_github_unrecognized_payload_returns_400() -> None:
    app = create_app(secret=SIGNING_VALUE)
    body = _body({"zen": "Keep it logically awesome."})

    response = _call(app, body, _sign(body), source="github")

    assert response.status_code == 400


def test_generic_source_still_uses_text_payload() -> None:
    app = create_app(secret=SIGNING_VALUE)
    body = _body({"text": "普通聊天"})

    response = _call(app, body, _sign(body), source="slack")

    assert response.status_code == 202
    assert _json(response) == {"decision": "DROP"}


def test_create_app_from_settings_with_secret_accepts_signed_request(tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        webhook_secret=SecretStr(SIGNING_VALUE),
    )
    workspace = _workspace(tmp_path)
    app = create_app_from_settings(settings, workspace=workspace)
    body = _body({"text": "/run-test smoke"})

    response = _call(app, body, _sign(body))

    assert response.status_code == 202
    assert _json(response) == {"decision": "FAST_PATH"}
    assert len(list(workspace.reports_dir.glob("test-*.json"))) == 1


def test_create_app_from_settings_without_secret_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENTINEL_WEBHOOK_SECRET", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    with pytest.raises(RuntimeError):
        create_app_from_settings(settings)


def test_create_app_from_settings_empty_secret_raises() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        webhook_secret=SecretStr(""),
    )

    with pytest.raises(RuntimeError):
        create_app_from_settings(settings)


def test_create_app_from_settings_loads_environment_when_omitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SENTINEL_WEBHOOK_SECRET", SIGNING_VALUE)
    workspace = _workspace(tmp_path)
    app = create_app_from_settings(workspace=workspace)
    body = _body({"text": "/run-test smoke"})

    response = _call(app, body, _sign(body))

    assert response.status_code == 202


def test_create_app_from_settings_wires_sync_docs(tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        webhook_secret=SecretStr(SIGNING_VALUE),
    )
    workspace = _workspace(tmp_path)
    app = create_app_from_settings(settings, workspace=workspace)
    body = _body({"text": "/sync-docs 更新说明"})

    response = _call(app, body, _sign(body))

    assert response.status_code == 202
    assert _json(response) == {"decision": "FAST_PATH"}
    changelog = workspace.docs_dir / "CHANGELOG.md"
    assert changelog.is_file()
    assert "更新说明" in changelog.read_text(encoding="utf-8")


def test_create_app_from_settings_command_handlers_override_defaults(tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        webhook_secret=SecretStr(SIGNING_VALUE),
    )
    workspace = _workspace(tmp_path)
    calls: list[str] = []
    app = create_app_from_settings(
        settings,
        workspace=workspace,
        command_handlers={"run-test": calls.append},
    )
    body = _body({"text": "/run-test smoke"})

    response = _call(app, body, _sign(body))

    assert response.status_code == 202
    assert calls == ["smoke"]
    assert list(workspace.reports_dir.glob("test-*.json")) == []


def test_create_app_from_settings_rejects_unsigned_request(tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        webhook_secret=SecretStr(SIGNING_VALUE),
    )
    app = create_app_from_settings(settings, workspace=_workspace(tmp_path))
    body = _body({"text": "/run-test smoke"})

    response = _call(app, body, None)

    assert response.status_code == 401
