"""软 / 硬阈值、压缩失败兜底与多角色隔离测试。"""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from agentscope.agent import Agent
from agentscope.message import Msg, UserMsg
from agentscope.model import ChatResponse

from agentsentinel.contracts import Checkpoint, ContextSummary, SessionState
from agentsentinel.roles.runtime import StubChatModel, text_reply
from agentsentinel.runtime.checkpoint import CheckpointStore, ContextSummaryStore
from agentsentinel.runtime.context_monitor import (
    CheckpointContext,
    ContextMonitor,
    ContextMonitorMiddleware,
    ContextUsage,
    DeterministicSummaryStrategy,
    MonitorAction,
)
from agentsentinel.runtime.launcher import RuntimeLauncher
from agentsentinel.workspace import Workspace

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def _respond(messages: list[Msg], tools: Sequence[dict[str, Any]]) -> ChatResponse:
    return text_reply("ok")


def _agent(name: str = "Coder", context_size: int = 1000) -> Agent:
    model = StubChatModel(_respond)
    model.context_size = context_size
    return Agent(name=name, system_prompt="test", model=model)


def _context(status: SessionState = SessionState.CODING) -> CheckpointContext:
    return CheckpointContext(
        status=status.value,
        artifacts=("code/main.py",),
        pending_action="run pytest",
        audit_cursor="2026-11-06T12:00:00Z#deadbeef0000",
    )


class SequenceCounter:
    """按调用顺序返回预设 token 数；复测不足时沿用最后一个值。"""

    def __init__(self, values: Sequence[int]) -> None:
        self._values = list(values)
        self.calls = 0

    async def count(
        self,
        agent: Agent,
        *,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> int:
        index = min(self.calls, len(self._values) - 1)
        self.calls += 1
        return self._values[index]


class FakeSummaryStrategy:
    def __init__(self, text: str = "native summary", error: Exception | None = None) -> None:
        self._text = text
        self._error = error
        self.calls = 0

    async def summarize(self, agent: Agent, usage: ContextUsage) -> str:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._text


class RecordingHandler:
    def __init__(self, store: CheckpointStore | None = None) -> None:
        self._store = store
        self.calls: list[tuple[Checkpoint, ContextSummary | None]] = []
        self.checkpoint_existed_at_call: list[bool] = []

    def handle(self, checkpoint: Checkpoint, summary: ContextSummary | None) -> None:
        self.calls.append((checkpoint, summary))
        if self._store is not None:
            self.checkpoint_existed_at_call.append(self._store.load(checkpoint.role) is not None)


def test_below_soft_threshold_writes_nothing(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    monitor = ContextMonitor(
        workspace=workspace,
        token_counter=SequenceCounter([300]),
        summary_strategy=FakeSummaryStrategy(),
    )

    result = asyncio.run(monitor.after_turn(_agent(), context=_context()))

    assert result.action is MonitorAction.OK
    assert not workspace.checkpoint_dir.exists()
    assert not workspace.context_summary_dir.exists()


def test_measure_counts_summary_when_context_is_empty(tmp_path: Path) -> None:
    """压缩后 context 可能为空，摘要仍会送模型，必须计入水位。"""
    agent = _agent(context_size=1000)
    agent.state.context = []
    agent.state.summary = "s" * 4000
    monitor = ContextMonitor(workspace=Workspace(tmp_path / "workspace"))

    usage = asyncio.run(monitor.measure(agent))

    assert usage.used_tokens >= 1000
    assert usage.ratio >= 1.0


def test_measure_includes_explicit_tool_schemas(tmp_path: Path) -> None:
    agent = _agent(context_size=1000)
    agent.state.context = []
    agent.state.summary = ""
    monitor = ContextMonitor(workspace=Workspace(tmp_path / "workspace"))
    baseline = asyncio.run(monitor.measure(agent))
    tools = [
        {
            "type": "function",
            "function": {
                "name": "demo_tool",
                "description": "d" * 4000,
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    with_tools = asyncio.run(monitor.measure(agent, tools=tools))

    assert with_tools.used_tokens > baseline.used_tokens + 500


def test_soft_threshold_writes_role_scoped_summary(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    monitor = ContextMonitor(
        workspace=workspace,
        token_counter=SequenceCounter([560]),
        summary_strategy=FakeSummaryStrategy("soft summary"),
    )

    result = asyncio.run(monitor.after_turn(_agent(), context=_context()))

    assert result.action is MonitorAction.SOFT_SUMMARIZED
    assert result.summary_path == workspace.context_summary_path("Coder")
    assert result.checkpoint_path is None
    summary = ContextSummaryStore(workspace).load("Coder")
    assert summary is not None
    assert (summary.used_tokens, summary.context_window, summary.ratio) == (560, 1000, 0.56)
    assert summary.summary == "soft summary"
    assert summary.strategy == "native"


def test_hard_threshold_compressed_below_hard_does_not_reset(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    handler = RecordingHandler()
    monitor = ContextMonitor(
        workspace=workspace,
        token_counter=SequenceCounter([950, 600]),
        summary_strategy=FakeSummaryStrategy("compressed summary"),
        hard_handler=handler,
    )

    result = asyncio.run(monitor.after_turn(_agent(), context=_context()))

    assert result.action is MonitorAction.HARD_COMPRESSED
    assert handler.calls == []
    assert result.checkpoint_path is None
    assert result.summary_path is not None
    assert result.summary_path.exists()
    assert not workspace.checkpoint_dir.exists()
    summary = ContextSummaryStore(workspace).load("Coder")
    assert summary is not None
    assert summary.used_tokens == 600
    assert summary.ratio == 0.6


def test_hard_threshold_still_high_writes_checkpoint_then_restarts_once(
    tmp_path: Path,
) -> None:
    workspace = Workspace(tmp_path / "workspace")
    store = CheckpointStore(workspace)
    handler = RecordingHandler(store)
    monitor = ContextMonitor(
        workspace=workspace,
        token_counter=SequenceCounter([950, 950]),
        summary_strategy=FakeSummaryStrategy("compressed summary"),
        hard_handler=handler,
    )

    result = asyncio.run(monitor.after_turn(_agent(), context=_context()))

    assert result.action is MonitorAction.HARD_RESTARTED
    assert len(handler.calls) == 1
    assert handler.checkpoint_existed_at_call == [True]
    assert result.checkpoint_path == workspace.checkpoint_path("Coder")
    checkpoint = store.load("Coder")
    assert checkpoint is not None
    assert checkpoint.role == "Coder"
    assert checkpoint.status is SessionState.CODING
    assert checkpoint.artifacts == ("code/main.py",)
    assert checkpoint.pending_action == "run pytest"
    assert checkpoint.audit_cursor == "2026-11-06T12:00:00Z#deadbeef0000"
    assert checkpoint.context_summary_path == "reports/context-summary/coder.json"
    assert checkpoint.created_at >= NOW


def test_hard_threshold_without_handler_reports_checkpointed(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    monitor = ContextMonitor(
        workspace=workspace,
        token_counter=SequenceCounter([950, 950]),
        summary_strategy=FakeSummaryStrategy(),
    )

    result = asyncio.run(monitor.after_turn(_agent(), context=_context()))

    assert result.action is MonitorAction.HARD_CHECKPOINTED
    assert result.checkpoint_path is not None
    assert result.checkpoint_path.exists()


def test_compression_failure_writes_checkpoint_before_reset(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    store = CheckpointStore(workspace)
    handler = RecordingHandler(store)
    monitor = ContextMonitor(
        workspace=workspace,
        token_counter=SequenceCounter([600]),
        summary_strategy=FakeSummaryStrategy(error=RuntimeError("no model")),
        hard_handler=handler,
    )

    result = asyncio.run(monitor.after_turn(_agent(), context=_context()))

    assert result.action is MonitorAction.HARD_RESTARTED
    assert handler.checkpoint_existed_at_call == [True]
    checkpoint = store.load("Coder")
    assert checkpoint is not None
    assert checkpoint.pending_action == "context_compression_failed"
    summary = ContextSummaryStore(workspace).load("Coder")
    assert summary is not None
    assert summary.strategy == "deterministic"
    assert "[deterministic]" in summary.summary


def test_deterministic_strategy_does_not_call_model() -> None:
    usage = ContextUsage(role="Coder", used_tokens=600, context_window=1000, ratio=0.6)
    strategy = DeterministicSummaryStrategy(
        pending_action="run pytest",
        artifacts=("code/main.py",),
    )

    text = asyncio.run(strategy.summarize(_agent(), usage))

    assert "role=Coder" in text
    assert "run pytest" in text
    assert "code/main.py" in text


def test_stub_backend_compression_failure_degrades_to_deterministic(tmp_path: Path) -> None:
    """stub 模型不联网；原生压缩抛错时落 deterministic 摘要 + checkpoint。"""
    workspace = Workspace(tmp_path / "workspace")
    model = StubChatModel(_respond)
    model.context_size = 200
    agent = Agent(name="Coder", system_prompt="test", model=model)
    agent.context_config.compression_fallback_to_truncation = False
    agent.state.context = [UserMsg("user", "x" * 1200)]
    handler = RecordingHandler()
    monitor = ContextMonitor(workspace=workspace, hard_handler=handler)

    result = asyncio.run(monitor.after_turn(agent, context=_context()))

    assert result.action is MonitorAction.HARD_RESTARTED
    assert len(handler.calls) == 1
    summary = ContextSummaryStore(workspace).load("Coder")
    assert summary is not None
    assert summary.strategy == "deterministic"
    assert summary.pending_action == "context_compression_failed"
    assert "[deterministic]" in summary.summary
    checkpoint = CheckpointStore(workspace).load("Coder")
    assert checkpoint is not None
    assert checkpoint.pending_action == "context_compression_failed"


def test_multiple_roles_keep_separate_checkpoint_and_summary_files(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    counter = SequenceCounter([950, 950])
    monitor = ContextMonitor(
        workspace=workspace,
        token_counter=counter,
        summary_strategy=FakeSummaryStrategy(),
    )

    asyncio.run(monitor.after_turn(_agent("Coder"), context=_context()))
    asyncio.run(monitor.after_turn(_agent("Tester"), context=_context()))

    checkpoints = CheckpointStore(workspace)
    summaries = ContextSummaryStore(workspace)
    assert checkpoints.load("Coder") is not None
    assert checkpoints.load("Tester") is not None
    assert summaries.load("Coder") is not None
    assert summaries.load("Tester") is not None
    assert workspace.checkpoint_path("Coder") != workspace.checkpoint_path("Tester")


def test_middleware_passes_events_through_and_monitors_after_reply(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    counter = SequenceCounter([300])
    monitor = ContextMonitor(
        workspace=workspace,
        token_counter=counter,
        summary_strategy=FakeSummaryStrategy(),
    )
    middleware = ContextMonitorMiddleware(monitor, lambda agent: _context())
    yielded: list[str] = []

    async def next_handler(**kwargs: Any) -> AsyncGenerator[str, None]:
        yield "start"
        yield "end"

    async def consume() -> None:
        async for event in middleware.on_reply(
            _agent(),
            {"inputs": None},
            next_handler,
        ):
            yielded.append(event)

    asyncio.run(consume())

    assert yielded == ["start", "end"]
    assert counter.calls == 1


def test_hard_threshold_with_launcher_restarts_agent_in_process(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    agent = _agent()
    agent.state.context = [UserMsg("user", "old turn")]
    launcher = RuntimeLauncher(workspace=workspace)
    launcher.register("Coder", agent)
    monitor = ContextMonitor(
        workspace=workspace,
        token_counter=SequenceCounter([950, 950]),
        summary_strategy=FakeSummaryStrategy("native summary"),
        hard_handler=launcher,
    )

    result = asyncio.run(monitor.after_turn(agent, context=_context()))

    assert result.action is MonitorAction.HARD_RESTARTED
    assert agent.state.context == []
    assert isinstance(agent.state.summary, str)
    assert "[resume] role=Coder status=coding" in agent.state.summary
    assert "[pending] run pytest" in agent.state.summary
    assert "[artifacts] code/main.py" in agent.state.summary
    assert "[audit_cursor] 2026-11-06T12:00:00Z#deadbeef0000" in agent.state.summary
    assert "native summary" in agent.state.summary


@pytest.mark.parametrize(
    ("soft", "hard"),
    [(0.9, 0.55), (0.55, 0.95), (0.55, 0.55)],
)
def test_threshold_validation_rejects_inverted_or_too_high(
    tmp_path: Path,
    soft: float,
    hard: float,
) -> None:
    workspace = Workspace(tmp_path / "workspace")

    with pytest.raises(ValueError):
        ContextMonitor(workspace=workspace, soft_ratio=soft, hard_ratio=hard)
