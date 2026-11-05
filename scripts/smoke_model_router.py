"""P2-3 真实端点冒烟：读取两档配置并发一次最小请求。

用法（真实 Key 只放本地 .env，不提交）::

    uv run python scripts/smoke_model_router.py

未配置真实端点时打印清晰提示并以非零码退出；不会伪造调用结果。
"""

import asyncio
import sys
from pathlib import Path
from typing import Any, cast

# 脚本以 `uv run python scripts/...` 直接运行时，把仓库根加入导入路径。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentscope.message import UserMsg
from agentscope.model import ChatModelBase, ChatResponse

from agentsentinel.config import Settings
from agentsentinel.models import ModelRouter


async def _ask_once(model: ChatModelBase) -> ChatResponse:
    response = cast(Any, model([UserMsg("user", "Reply with the single word: ok")]))
    return cast(ChatResponse, await response)


def _response_text(response: ChatResponse) -> str:
    content = getattr(response, "content", None) or []
    parts = [
        block.text
        for block in content
        if getattr(block, "type", "") == "text" and getattr(block, "text", "")
    ]
    return "".join(parts) or "<no text content>"


def main() -> int:
    settings = Settings()
    if settings.model_backend != "real":
        print(
            "SENTINEL_MODEL_BACKEND 当前不是 real；冒烟需要真实 OpenAI 兼容端点。",
            file=sys.stderr,
        )
        return 2

    for tier, profile in (
        ("premium", settings.premium_model),
        ("economy", settings.economy_model),
    ):
        print(
            f"{tier}: base_url={profile.base_url or '(OpenAI 默认端点)'} "
            f"model={profile.model or '(未配置)'}"
        )

    router = ModelRouter(settings)
    try:
        premium = router.for_role("coder")
        router.for_role("orchestrator")
    except RuntimeError as exc:
        print(f"配置缺失：{exc}", file=sys.stderr)
        return 2
    if premium is None:
        print("premium 档未解析到模型，无法冒烟。", file=sys.stderr)
        return 2

    try:
        response = asyncio.run(_ask_once(premium))
    except Exception as exc:  # noqa: BLE001 - 冒烟需如实报告任何调用失败
        print(f"调用失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"response: {_response_text(response)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
