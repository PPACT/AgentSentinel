"""Orchestrator 正常编排、连续失败回退与权限边界。"""

import json
from pathlib import Path

import pytest

from agentsentinel.contracts import TestStatus
from agentsentinel.governance import MatrixPermissionPolicy, RegexSanitizer
from agentsentinel.roles import (
    AgentResult,
    Coder,
    Manager,
    Orchestrator,
    PipelineOutcome,
    Tester,
    Writer,
)
from agentsentinel.tools import StubGitOps
from agentsentinel.workspace import Workspace


class _AlwaysFailingTester(Tester):
    def handle(self, task: str, workspace: Workspace, *, feedback: str = "") -> AgentResult:
        return AgentResult(
            agent=self.name,
            summary="forced failure",
            status=TestStatus.FAIL,
        )


def _build_orchestrator(
    tmp_path: Path,
    tester: Tester | None = None,
    *,
    baseline_ref: str = "baseline-x",
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
        coder=Coder(),
        tester=tester if tester is not None else Tester(),
        writer=Writer(),
        manager=manager,
        baseline_ref=baseline_ref,
    )
    return workspace, orchestrator, git_ops, manager


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
    workspace, orchestrator, git_ops, manager = _build_orchestrator(
        tmp_path, _AlwaysFailingTester()
    )

    result = orchestrator.run("离线演示任务", workspace)

    assert result.status is PipelineOutcome.FAILED
    assert result.attempts == 2
    assert ("reset_to_baseline", "baseline-x") in git_ops.calls
    assert ("push", "") not in git_ops.calls
    records = [
        json.loads(line) for line in manager.audit_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["action_type"] == "revert"
    assert records[-1]["reason"] == "SCAN_NOT_REQUIRED"


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
