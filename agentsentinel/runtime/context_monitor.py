"""上下文水位监控：软阈值摘要、硬阈值压缩 / checkpoint / reset。

判定顺序严格按 ``docs/local/runtime-context-design.md`` §4：

1. ``ratio >= hard``：先调原生压缩并**重新测量**；仍不降则写 checkpoint 后
   交给 ``hard_handler``（默认由 ``RuntimeLauncher`` 负责 reset）。
2. ``soft <= ratio < hard``：压缩并写按角色分文件的摘要。
3. 其余：不写盘。

压缩抛错时不静默继续：写确定性降级摘要 + checkpoint
（``pending_action=context_compression_failed``）再交给 handler。
"""

from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from agentscope.agent import Agent
from agentscope.message import DataBlock, TextBlock
from agentscope.middleware import MiddlewareBase

from agentsentinel.contracts import Checkpoint, ContextSummary, SessionState
from agentsentinel.runtime.checkpoint import CheckpointStore, ContextSummaryStore
from agentsentinel.workspace import Workspace

COMPRESSION_FAILED = "context_compression_failed"
_MAX_TRIGGER_RATIO = 0.9
_MIN_TRIGGER_RATIO = 0.101


@dataclass(frozen=True)
class ContextUsage:
    role: str
    used_tokens: int
    context_window: int
    ratio: float


class MonitorAction(StrEnum):
    OK = "ok"
    SOFT_SUMMARIZED = "soft_summarized"
    HARD_COMPRESSED = "hard_compressed"
    HARD_CHECKPOINTED = "hard_checkpointed"
    HARD_RESTARTED = "hard_restarted"


@dataclass(frozen=True)
class MonitorResult:
    usage: ContextUsage
    action: MonitorAction
    summary_path: Path | None = None
    checkpoint_path: Path | None = None


@dataclass(frozen=True)
class CheckpointContext:
    """调用方（流水线 / Manager）提供的 checkpoint 元数据。"""

    status: str
    artifacts: tuple[str, ...] = ()
    pending_action: str = ""
    audit_cursor: str = ""


class TokenCounter(Protocol):
    async def count(
        self,
        agent: Agent,
        *,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> int: ...


class SummaryStrategy(Protocol):
    async def summarize(self, agent: Agent, usage: ContextUsage) -> str: ...


class HardThresholdHandler(Protocol):
    def handle(self, checkpoint: Checkpoint, summary: ContextSummary | None) -> None: ...


class AgentScopeTokenCounter:
    """直接复用 AgentScope 的 ``count_tokens``；子类覆盖时自动更准。"""

    async def count(
        self,
        agent: Agent,
        *,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> int:
        return await agent.model.count_tokens(
            agent.state.context,
            list(tools) if tools is not None else None,
        )


def _serialize_context_summary(value: str | list[TextBlock | DataBlock]) -> str:
    if isinstance(value, str):
        return value
    parts: list[str] = []
    for block in value:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        else:
            parts.append(block.model_dump_json())
    return "\n".join(parts)


class NativeSummaryStrategy:
    """复用 AgentScope 原生 ``compress_context``，不另造压缩算法。"""

    async def summarize(self, agent: Agent, usage: ContextUsage) -> str:
        # 触发点取本次实测水位，并夹在 ContextConfig 允许的区间内；
        # 复测由 ContextMonitor 负责，不能沿用压缩前水位。
        trigger = min(_MAX_TRIGGER_RATIO, max(_MIN_TRIGGER_RATIO, usage.ratio))
        config = agent.context_config.model_copy(update={"trigger_ratio": trigger})
        await agent.compress_context(context_config=config)
        return _serialize_context_summary(agent.state.summary)


@dataclass(frozen=True)
class DeterministicSummaryStrategy:
    """不调模型的降级摘要；用于 stub 后端或原生压缩失败。"""

    pending_action: str = ""
    artifacts: tuple[str, ...] = ()

    async def summarize(self, agent: Agent, usage: ContextUsage) -> str:
        lines = [
            f"[deterministic] role={agent.name}",
            f"[usage] {usage.used_tokens}/{usage.context_window} ({usage.ratio:.3f})",
        ]
        if self.pending_action:
            lines.append(f"[pending] {self.pending_action}")
        if self.artifacts:
            lines.append(f"[artifacts] {'; '.join(self.artifacts)}")
        return "\n".join(lines)


def _coerce_status(value: str) -> SessionState:
    return value if isinstance(value, SessionState) else SessionState(value)


class ContextMonitor:
    """在 Agent 循环外测量水位，并按阈值执行摘要 / checkpoint / reset。"""

    def __init__(
        self,
        *,
        workspace: Workspace,
        soft_ratio: float = 0.55,
        hard_ratio: float = 0.90,
        token_counter: TokenCounter | None = None,
        summary_strategy: SummaryStrategy | None = None,
        checkpoint_store: CheckpointStore | None = None,
        summary_store: ContextSummaryStore | None = None,
        hard_handler: HardThresholdHandler | None = None,
    ) -> None:
        if not 0 < soft_ratio < hard_ratio <= _MAX_TRIGGER_RATIO:
            raise ValueError(
                f"阈值必须满足 0 < soft < hard <= 0.9；当前为 {soft_ratio} / {hard_ratio}"
            )
        self._workspace = workspace
        self._soft_ratio = soft_ratio
        self._hard_ratio = hard_ratio
        self._token_counter: TokenCounter = token_counter or AgentScopeTokenCounter()
        self._summary_strategy: SummaryStrategy = summary_strategy or NativeSummaryStrategy()
        self._checkpoint_store = checkpoint_store or CheckpointStore(workspace)
        self._summary_store = summary_store or ContextSummaryStore(workspace)
        self._hard_handler = hard_handler

    async def measure(
        self,
        agent: Agent,
        *,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> ContextUsage:
        used_tokens = await self._token_counter.count(agent, tools=tools)
        context_window = int(agent.model.context_size)
        ratio = used_tokens / context_window if context_window > 0 else 0.0
        return ContextUsage(
            role=agent.name,
            used_tokens=used_tokens,
            context_window=context_window,
            ratio=ratio,
        )

    async def after_turn(
        self,
        agent: Agent,
        *,
        context: CheckpointContext,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> MonitorResult:
        usage = await self.measure(agent, tools=tools)

        if usage.ratio >= self._hard_ratio:
            try:
                summary_text = await self._summary_strategy.summarize(agent, usage)
            except Exception:  # noqa: BLE001 - 压缩失败必须走兜底，不能静默继续
                return await self._handle_compression_failure(
                    agent,
                    context=context,
                    usage=usage,
                    tools=tools,
                )

            # 硬阈值必须先压缩并复测；复测仍高才 checkpoint + reset。
            rechecked = await self.measure(agent, tools=tools)
            if rechecked.ratio < self._hard_ratio:
                summary, summary_path = self._record_summary(
                    usage=rechecked,
                    text=summary_text,
                    pending_action=context.pending_action,
                    strategy=self._strategy_name(),
                )
                return MonitorResult(
                    usage=rechecked,
                    action=MonitorAction.HARD_COMPRESSED,
                    summary_path=summary_path,
                )

            summary, summary_path = self._record_summary(
                usage=rechecked,
                text=summary_text,
                pending_action=context.pending_action,
                strategy=self._strategy_name(),
            )
            return self._checkpoint_and_restart(
                usage=rechecked,
                context=context,
                summary=summary,
                summary_path=summary_path,
                pending_action=context.pending_action,
            )

        if usage.ratio >= self._soft_ratio:
            try:
                summary_text = await self._summary_strategy.summarize(agent, usage)
            except Exception:  # noqa: BLE001 - 压缩失败必须走兜底，不能静默继续
                return await self._handle_compression_failure(
                    agent,
                    context=context,
                    usage=usage,
                    tools=tools,
                )
            rechecked = await self.measure(agent, tools=tools)
            _, summary_path = self._record_summary(
                usage=rechecked,
                text=summary_text,
                pending_action=context.pending_action,
                strategy=self._strategy_name(),
            )
            return MonitorResult(
                usage=rechecked,
                action=MonitorAction.SOFT_SUMMARIZED,
                summary_path=summary_path,
            )

        return MonitorResult(usage=usage, action=MonitorAction.OK)

    async def _handle_compression_failure(
        self,
        agent: Agent,
        *,
        context: CheckpointContext,
        usage: ContextUsage,
        tools: Sequence[dict[str, Any]] | None,
    ) -> MonitorResult:
        fallback = DeterministicSummaryStrategy(
            pending_action=COMPRESSION_FAILED,
            artifacts=context.artifacts,
        )
        text = await fallback.summarize(agent, usage)
        try:
            rechecked = await self.measure(agent, tools=tools)
        except Exception:  # noqa: BLE001 - 复测失败时退回压缩前水位
            rechecked = usage
        summary, summary_path = self._record_summary(
            usage=rechecked,
            text=text,
            pending_action=COMPRESSION_FAILED,
            strategy="deterministic",
        )
        return self._checkpoint_and_restart(
            usage=rechecked,
            context=context,
            summary=summary,
            summary_path=summary_path,
            pending_action=COMPRESSION_FAILED,
        )

    def _checkpoint_and_restart(
        self,
        *,
        usage: ContextUsage,
        context: CheckpointContext,
        summary: ContextSummary,
        summary_path: Path,
        pending_action: str,
    ) -> MonitorResult:
        checkpoint = Checkpoint(
            role=usage.role,
            status=_coerce_status(context.status),
            artifacts=context.artifacts,
            pending_action=pending_action,
            audit_cursor=context.audit_cursor,
            context_summary_path=self._relative(summary_path),
            created_at=datetime.now(UTC),
        )
        checkpoint_path = self._checkpoint_store.save(checkpoint)
        restarted = self._hard_handler is not None
        if self._hard_handler is not None:
            self._hard_handler.handle(checkpoint, summary)
        return MonitorResult(
            usage=usage,
            action=MonitorAction.HARD_RESTARTED if restarted else MonitorAction.HARD_CHECKPOINTED,
            summary_path=summary_path,
            checkpoint_path=checkpoint_path,
        )

    def _record_summary(
        self,
        *,
        usage: ContextUsage,
        text: str,
        pending_action: str,
        strategy: Literal["native", "deterministic"],
    ) -> tuple[ContextSummary, Path]:
        summary = ContextSummary(
            role=usage.role,
            generated_at=datetime.now(UTC),
            used_tokens=usage.used_tokens,
            context_window=usage.context_window,
            ratio=usage.ratio,
            summary=text,
            pending_action=pending_action,
            strategy=strategy,
        )
        return summary, self._summary_store.save(summary)

    def _strategy_name(self) -> Literal["native", "deterministic"]:
        if isinstance(self._summary_strategy, DeterministicSummaryStrategy):
            return "deterministic"
        return "native"

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self._workspace.root).as_posix()
        except ValueError:
            return path.as_posix()


class ContextMonitorMiddleware(MiddlewareBase):
    """AgentScope 原生 Hook 适配层；事件原样透传，回合结束后调用 after_turn。"""

    def __init__(
        self,
        monitor: ContextMonitor,
        context_provider: Callable[[Agent], CheckpointContext],
    ) -> None:
        self._monitor = monitor
        self._context_provider = context_provider

    async def on_reply(
        self,
        agent: Agent,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[Any, None]],
    ) -> AsyncGenerator[Any, None]:
        async for event in next_handler(**input_kwargs):
            yield event
        await self._monitor.after_turn(
            agent,
            context=self._context_provider(agent),
        )
