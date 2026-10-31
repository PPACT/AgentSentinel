"""离线角色运行时：stub 模型与同步驱动 AgentScope Agent。

AgentScope 2.0.8 的 ``Agent.reply`` 是异步接口；离线编排用脚本模型驱动，
不联网、不读取 API Key。真实模型仍走同一接口，只需换入 ``ChatModelBase`` 实现。
"""

import asyncio
import json
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from agentscope.agent import Agent
from agentscope.credential import CredentialBase
from agentscope.formatter import FormatterBase
from agentscope.message import Msg, TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse

Responder = Callable[[list[Msg], Sequence[dict[str, Any]]], ChatResponse]


class StubFormatter(FormatterBase):
    """stub 模型的占位 formatter；只声明纯文本输入，不参与任何 API 调用。"""

    async def format(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []


class StubChatModel(ChatModelBase):
    """完全离线的脚本模型：每次调用交给 responder 生成预置响应。"""

    def __init__(self, responder: Responder) -> None:
        super().__init__(
            credential=CredentialBase(),
            model="stub-offline",
            parameters=ChatModelBase.Parameters(),
            stream=False,
        )
        self.formatter = StubFormatter()
        self._responder = responder
        self.calls = 0

    @property
    def call_count(self) -> int:
        return self.calls

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls += 1
        return self._responder(list(messages), tuple(tools or ()))


def tool_call(name: str, arguments: dict[str, Any], *, call_id: str | None = None) -> ChatResponse:
    """构造一次工具调用响应；input 必须是 JSON 字符串。"""
    return ChatResponse(
        content=[
            ToolCallBlock(
                id=call_id or f"call-{uuid.uuid4().hex[:8]}",
                name=name,
                input=json.dumps(arguments, ensure_ascii=False),
            )
        ],
        is_last=True,
    )


def text_reply(text: str) -> ChatResponse:
    """构造一次纯文本响应，结束当前 ReAct 回合。"""
    return ChatResponse(content=[TextBlock(text=text)], is_last=True)


async def _reply(agent: Agent, message: Msg) -> Msg:
    return await agent.reply(message)


def run_agent_sync(agent: Agent, message: Msg) -> Msg:
    """在同步编排中执行一次 Agent 回复；禁止嵌套在已有事件循环里调用。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_reply(agent, message))
    raise RuntimeError("离线编排不能在已运行的事件循环中同步调用")
