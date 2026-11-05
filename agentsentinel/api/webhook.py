"""入口治理：FastAPI Webhook 骨架。

只做验签、分类与分发。Slow Path 交给 ``BackgroundTasks``，不在请求线程内跑流水线。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

from agentsentinel.api.adapters import parse_github_event
from agentsentinel.api.commands import (
    CommandHandler,
    default_command_handlers,
    dispatch,
)
from agentsentinel.api.ingress import (
    IngressDecision,
    IngressEvent,
    classify,
    verify_signature,
)
from agentsentinel.config import Settings
from agentsentinel.workspace import Workspace

SIGNATURE_HEADER = "X-Hub-Signature-256"

_PipelineRunner = Callable[[str], object]


def _default_pipeline_runner(task: str) -> object:
    # 延迟导入：仅构造应用骨架时不初始化 AgentScope 流水线。
    from agentsentinel.pipeline import SentinelPipeline

    return SentinelPipeline().run(task)


def _parse_event(source: str, body: bytes) -> IngressEvent | None:
    try:
        payload: Any = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if source == "github":
        return parse_github_event(payload, source)
    text = payload.get("text")
    if not isinstance(text, str):
        return None
    raw_labels = payload.get("labels", [])
    labels: tuple[str, ...] = ()
    if isinstance(raw_labels, list):
        labels = tuple(item for item in raw_labels if isinstance(item, str))
    return IngressEvent(source=source, text=text, labels=labels)


def create_app(
    *,
    secret: str,
    command_handler: Callable[[str, str], None] | None = None,
    pipeline_runner: _PipelineRunner | None = None,
) -> FastAPI:
    """构造 Webhook 应用；处理器与流水线运行器可注入，便于离线测试。"""
    runner = pipeline_runner if pipeline_runner is not None else _default_pipeline_runner
    app = FastAPI(title="AgentSentinel Ingress")

    @app.post("/api/webhook/{source}")
    async def receive_webhook(
        source: str,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> JSONResponse:
        body = await request.body()
        signature = request.headers.get(SIGNATURE_HEADER)
        if not verify_signature(secret=secret, body=body, header=signature):
            return JSONResponse({"detail": "invalid signature"}, status_code=401)

        event = _parse_event(source, body)
        if event is None:
            return JSONResponse({"detail": "invalid payload"}, status_code=400)

        result = classify(event)
        if result.decision is IngressDecision.FAST_PATH:
            if command_handler is not None and result.command is not None:
                command_handler(result.command, result.arguments)
        elif result.decision is IngressDecision.SLOW_PATH:
            background_tasks.add_task(runner, result.task)
        return JSONResponse({"decision": result.decision.value}, status_code=202)

    return app


def create_app_from_settings(
    settings: Settings | None = None,
    *,
    workspace: Workspace | None = None,
    command_handlers: Mapping[str, CommandHandler] | None = None,
) -> FastAPI:
    """按运行时配置构造应用；Webhook 密钥缺失或为空时拒绝启动。

    Fast Path 默认接生产处理器：固定命令直接调用角色 ``handle``，不经过模型。
    传入的 ``command_handlers`` 覆盖同名默认处理器。
    """
    resolved = settings if settings is not None else Settings()
    secret = resolved.webhook_secret
    secret_value = secret.get_secret_value() if secret is not None else ""
    if not secret_value:
        raise RuntimeError("SENTINEL_WEBHOOK_SECRET 未配置，拒绝启动 Webhook")
    resolved_workspace = workspace if workspace is not None else Workspace()
    resolved_workspace.ensure()
    handlers = default_command_handlers(resolved_workspace)
    if command_handlers is not None:
        handlers.update(command_handlers)
    return create_app(
        secret=secret_value,
        command_handler=lambda command, arguments: dispatch(handlers, command, arguments),
    )
