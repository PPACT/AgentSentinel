"""ContextResetter 与 RuntimeLauncher 的进程内恢复测试。"""

import logging
from collections.abc import Sequence
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
from agentsentinel.runtime.context_reset import ContextResetter
from agentsentinel.runtime.launcher import CheckpointNotFound, RuntimeLauncher
from agentsentinel.workspace import Workspace

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def _respond(messages: list[Msg], tools: Sequence[dict[str, Any]]) -> ChatResponse:
    return text_reply("ok")


def _agent(name: str = "Coder") -> Agent:
    return Agent(name=name, system_prompt="test", model=StubChatModel(_respond))


def _checkpoint(role: str = "Coder") -> Checkpoint:
    return Checkpoint(
        role=role,
        status=SessionState.TEST_FAILED,
        artifacts=("code/main.py", "reports/test-20261106T120000Z.json"),
        pending_action="修复失败测试后重跑 pytest",
        audit_cursor="2026-11-06T12:00:00Z#deadbeef0000",
        context_summary_path=f"reports/context-summary/{role.casefold()}.json",
        created_at=NOW,
    )


def _summary(role: str = "Coder") -> ContextSummary:
    return ContextSummary(
        role=role,
        generated_at=NOW,
        used_tokens=950,
        context_window=1000,
        ratio=0.95,
        summary="压缩摘要正文",
        pending_action="修复失败测试后重跑 pytest",
        strategy="native",
    )


def test_build_resume_text_contains_pointers_and_is_bounded(tmp_path: Path) -> None:
    resetter = ContextResetter(workspace=Workspace(tmp_path / "workspace"))
    summary = _summary().model_copy(update={"summary": "\n".join(f"line-{i}" for i in range(100))})

    text = resetter.build_resume_text(_checkpoint(), summary)

    assert "[resume] role=Coder status=test_failed" in text
    assert "[pending] 修复失败测试后重跑 pytest" in text
    assert "[artifacts] code/main.py; reports/test-20261106T120000Z.json" in text
    assert "[audit_cursor] 2026-11-06T12:00:00Z#deadbeef0000" in text
    assert "line-0" in text
    assert "line-99" not in text
    assert len(text.splitlines()) <= 40


def test_reset_clears_context_and_keeps_task_side_state(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    agent = _agent()
    agent.state.context = [UserMsg("user", "old turn")]
    agent.state.reply_context.cur_iter = 3
    permission_context = agent.state.permission_context
    tool_context = agent.state.tool_context
    tasks_context = agent.state.tasks_context

    ContextResetter(workspace=workspace).reset(agent, _checkpoint(), _summary())

    assert agent.state.context == []
    assert isinstance(agent.state.summary, str)
    assert agent.state.summary.startswith("[resume] role=Coder status=test_failed")
    assert "压缩摘要正文" in agent.state.summary
    assert agent.state.reply_context.cur_iter == 0
    assert agent.state.permission_context is permission_context
    assert agent.state.tool_context is tool_context
    assert agent.state.tasks_context is tasks_context


def test_reset_without_summary_uses_checkpoint_fields_only(tmp_path: Path) -> None:
    agent = _agent()

    ContextResetter(workspace=Workspace(tmp_path / "workspace")).reset(agent, _checkpoint())

    assert isinstance(agent.state.summary, str)
    assert "(no summary)" in agent.state.summary
    assert "code/main.py" in agent.state.summary


def test_launcher_resume_returns_none_for_new_session(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    agent = _agent()
    agent.state.context = [UserMsg("user", "keep me")]

    assert RuntimeLauncher(workspace=workspace).resume("Coder", agent) is None
    assert len(agent.state.context) == 1


def test_launcher_resume_restores_from_checkpoint(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    store = CheckpointStore(workspace)
    summary_store = ContextSummaryStore(workspace)
    checkpoint = _checkpoint()
    store.save(checkpoint)
    summary_store.save(_summary())
    agent = _agent()
    agent.state.context = [UserMsg("user", "old turn")]

    resumed = RuntimeLauncher(workspace=workspace).resume("Coder", agent)

    assert resumed == checkpoint
    assert agent.state.context == []
    assert "压缩摘要正文" in str(agent.state.summary)


def test_launcher_restart_without_checkpoint_raises(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    launcher = RuntimeLauncher(workspace=workspace)
    launcher.register("Coder", _agent())

    with pytest.raises(CheckpointNotFound):
        launcher.restart("Coder")


def test_launcher_restart_requires_registered_agent(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    CheckpointStore(workspace).save(_checkpoint())
    launcher = RuntimeLauncher(workspace=workspace)

    with pytest.raises(RuntimeError, match="未注册"):
        launcher.restart("Coder")


def test_launcher_restart_resets_registered_agent(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    CheckpointStore(workspace).save(_checkpoint())
    ContextSummaryStore(workspace).save(_summary())
    agent = _agent()
    agent.state.context = [UserMsg("user", "old turn")]
    launcher = RuntimeLauncher(workspace=workspace)
    launcher.register("Coder", agent)

    result = launcher.restart("Coder")

    assert result.restarted is True
    assert result.checkpoint.status is SessionState.TEST_FAILED
    assert result.summary is not None
    assert agent.state.context == []
    assert "[resume] role=Coder status=test_failed" in str(agent.state.summary)


def test_launcher_handle_delegates_to_restart(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    CheckpointStore(workspace).save(_checkpoint())
    agent = _agent()
    agent.state.context = [UserMsg("user", "old turn")]
    launcher = RuntimeLauncher(workspace=workspace)
    launcher.register("Coder", agent)

    launcher.handle(_checkpoint(), _summary())

    assert agent.state.context == []


def test_launcher_duplicate_register_warns_and_overwrites(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace = Workspace(tmp_path / "workspace")
    CheckpointStore(workspace).save(_checkpoint())
    launcher = RuntimeLauncher(workspace=workspace)
    first = _agent()
    second = _agent()
    first.state.context = [UserMsg("user", "first")]
    second.state.context = [UserMsg("user", "second")]

    launcher.register("Coder", first)
    with caplog.at_level(logging.WARNING):
        launcher.register("coder", second)
    launcher.restart("Coder")

    assert "重复注册" in caplog.text
    assert len(first.state.context) == 1
    assert second.state.context == []
