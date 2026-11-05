"""Orchestrator 正常编排、连续失败回退与权限边界。"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from agentscope.message import Msg, ToolResultBlock
from agentscope.model import ChatModelBase, ChatResponse

from agentsentinel.contracts import TestStatus
from agentsentinel.governance import MatrixPermissionPolicy, RegexSanitizer
from agentsentinel.pipeline import supervisor_responder
from agentsentinel.roles import (
    AgentResult,
    Coder,
    Manager,
    Orchestrator,
    PipelineOutcome,
    StubChatModel,
    Tester,
    Writer,
)
from agentsentinel.roles.orchestrator import OrchestrationSession, SessionState
from agentsentinel.tools import StubGitOps
from agentsentinel.workspace import Workspace


class _AlwaysFailingTester(Tester):
    def __init__(self) -> None:
        self.calls = 0

    def handle(
        self,
        task: str,
        workspace: Workspace,
        *,
        feedback: str = "",
        model: ChatModelBase | None = None,
    ) -> AgentResult:
        self.calls += 1
        return AgentResult(
            agent=self.name,
            summary="forced failure",
            status=TestStatus.FAIL,
        )


class _FailThenPassTester(Tester):
    """第一次测试失败，Coder 局部修复后第二次通过。"""

    def __init__(self) -> None:
        self.calls = 0
        self.feedback_seen: list[str] = []

    def handle(
        self,
        task: str,
        workspace: Workspace,
        *,
        feedback: str = "",
        model: ChatModelBase | None = None,
    ) -> AgentResult:
        self.calls += 1
        self.feedback_seen.append(feedback)
        if self.calls == 1:
            return AgentResult(
                agent=self.name,
                summary="stub check failed: main missing",
                status=TestStatus.FAIL,
            )
        return super().handle(task, workspace, feedback=feedback, model=model)


class _CountingCoder(Coder):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def handle(
        self,
        task: str,
        workspace: Workspace,
        *,
        feedback: str = "",
        model: ChatModelBase | None = None,
    ) -> AgentResult:
        self.calls.append(feedback)
        return super().handle(task, workspace, feedback=feedback, model=model)


def _build_orchestrator(
    tmp_path: Path,
    tester: Tester | None = None,
    *,
    coder: Coder | None = None,
    baseline_ref: str | None = "baseline-x",
) -> tuple[Workspace, Orchestrator, StubGitOps, Manager]:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure()
    git_ops = StubGitOps()
    manager = Manager(
        workspace=workspace,
        sanitizer=RegexSanitizer(),
        git_ops=git_ops,
        policy=MatrixPermissionPolicy(),
    )
    orchestrator = Orchestrator(
        coder=coder if coder is not None else Coder(),
        tester=tester if tester is not None else Tester(),
        writer=Writer(),
        manager=manager,
        baseline_ref=baseline_ref,
    )
    return workspace, orchestrator, git_ops, manager


def _transitions(path: Path) -> list[tuple[str, str]]:
    return [
        (str(record["from_state"]), str(record["to_state"]))
        for record in (
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    ]


def _record_tool_outputs(messages: list[Msg], outputs: list[str]) -> None:
    for message in messages:
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                outputs.append(str(block.output))


def test_normal_run_publishes_once(tmp_path: Path) -> None:
    workspace, orchestrator, git_ops, manager = _build_orchestrator(tmp_path)

    result = orchestrator.run("离线演示任务", workspace)

    assert result.status is PipelineOutcome.PASSED
    assert result.attempts == 1
    assert ("push", "") in git_ops.calls
    records = [
        json.loads(line) for line in manager.audit_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["action_type"] == "push"


def test_two_failures_trigger_revert_once(tmp_path: Path) -> None:
    coder = _CountingCoder()
    tester = _AlwaysFailingTester()
    workspace, orchestrator, git_ops, manager = _build_orchestrator(tmp_path, tester, coder=coder)

    result = orchestrator.run("离线演示任务", workspace)

    assert result.status is PipelineOutcome.FAILED
    assert result.attempts == 2
    # Coder/Tester 在会话内各执行两次；耗尽后只回退一次。
    assert len(coder.calls) == 2
    assert tester.calls == 2
    assert ("reset_to_baseline", "baseline-x") in git_ops.calls
    assert ("push", "") not in git_ops.calls
    assert _transitions(workspace.pipeline_state_path) == [
        ("coding", "coded"),
        ("coded", "test_failed"),
        ("test_failed", "coded"),
        ("coded", "reverted"),
    ]
    records = [
        json.loads(line) for line in manager.audit_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["action_type"] == "revert"
    assert records[-1]["reason"] == "SCAN_NOT_REQUIRED"


def test_first_failure_repairs_locally_then_passes(tmp_path: Path) -> None:
    coder = _CountingCoder()
    tester = _FailThenPassTester()
    workspace, orchestrator, git_ops, _ = _build_orchestrator(tmp_path, tester, coder=coder)

    result = orchestrator.run("离线演示任务", workspace)

    assert result.status is PipelineOutcome.PASSED
    assert result.attempts == 1
    assert len(coder.calls) == 2
    assert tester.calls == 2
    assert coder.calls[1] == "stub check failed: main missing"
    assert ("push", "") in git_ops.calls
    assert _transitions(workspace.pipeline_state_path) == [
        ("coding", "coded"),
        ("coded", "test_failed"),
        ("test_failed", "coded"),
        ("coded", "test_passed"),
        ("test_passed", "doc_ready"),
        ("doc_ready", "published"),
    ]


def test_local_repair_reports_only_final_conclusion(tmp_path: Path) -> None:
    coder = _CountingCoder()
    tester = _FailThenPassTester()
    workspace, orchestrator, _, _ = _build_orchestrator(tmp_path, tester, coder=coder)
    session: OrchestrationSession = orchestrator.open_session("离线演示任务", workspace)

    session.assign("Coder")
    reported = session.assign("Tester")

    # 中间失败摘要只进反馈/状态流，TeamAssign 返回给主编排模型的是最终结论。
    assert tester.calls == 2
    assert "stub check failed" not in reported
    assert "重测通过" in reported
    assert session.state is SessionState.TEST_PASSED


def test_model_driven_path_keeps_local_repair_out_of_orchestrator_context(
    tmp_path: Path,
) -> None:
    coder = _CountingCoder()
    tester = _FailThenPassTester()
    workspace, orchestrator, git_ops, _ = _build_orchestrator(tmp_path, tester, coder=coder)
    tool_outputs: list[str] = []

    def model_factory(session: OrchestrationSession) -> StubChatModel:
        base = supervisor_responder(session)

        def respond(messages: list[Msg], tools: Sequence[dict[str, Any]]) -> ChatResponse:
            _record_tool_outputs(messages, tool_outputs)
            return base(messages, tools)

        return StubChatModel(respond)

    result = orchestrator.run_with_model("离线演示任务", workspace, model_factory)

    assert result.status is PipelineOutcome.PASSED
    assert len(coder.calls) == 2
    assert tester.calls == 2
    assert ("push", "") in git_ops.calls
    assert not any("stub check failed" in output for output in tool_outputs)


def test_exhausted_retries_without_baseline_block(tmp_path: Path) -> None:
    coder = _CountingCoder()
    tester = _AlwaysFailingTester()
    workspace, orchestrator, git_ops, _ = _build_orchestrator(
        tmp_path, tester, coder=coder, baseline_ref=None
    )

    result = orchestrator.run("离线演示任务", workspace)

    assert result.status is PipelineOutcome.FAILED
    assert "基线" in result.summary
    assert len(coder.calls) == 2
    assert tester.calls == 2
    assert not any(call[0] == "reset_to_baseline" for call in git_ops.calls)
    assert _transitions(workspace.pipeline_state_path) == [
        ("coding", "coded"),
        ("coded", "test_failed"),
        ("test_failed", "coded"),
        ("coded", "blocked"),
    ]


def test_orchestrator_has_no_direct_write_or_git_tools() -> None:
    assert "file_write" not in Orchestrator.tools
    assert "file_edit" not in Orchestrator.tools
    assert not any(tool.startswith("git") for tool in Orchestrator.tools)
    assert "git_reset_to_baseline" in Orchestrator.spec.forbidden_tools


def test_orchestrator_requires_positive_attempt_budget(tmp_path: Path) -> None:
    _, _, _, manager = _build_orchestrator(tmp_path)

    with pytest.raises(ValueError):
        Orchestrator(
            coder=Coder(),
            tester=Tester(),
            writer=Writer(),
            manager=manager,
            max_attempts=0,
        )
