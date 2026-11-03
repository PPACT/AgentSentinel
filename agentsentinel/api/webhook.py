"""入口治理：FastAPI Webhook 骨架。

只做验签、分类与分发。Slow Path 交给 ``BackgroundTasks``，不在请求线程内跑流水线。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

from agentsentinel.api.ingress import (
    IngressDecision,
    IngressEvent,
    classify,
    verify_signature,
)

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
