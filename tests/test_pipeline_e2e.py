"""M4 离线端到端：脚本模型驱动完整八步流水线。

覆盖审计字段、DENY 终态、连续失败回退、真实基线 tag、Toolkit 绑定、
上下文卸载，以及全程无外部网络与真实推送。
"""

import asyncio
import base64
import json
import socket
from pathlib import Path

import pytest
from agentscope.agent import Agent
from agentscope.message import Base64Source, DataBlock, URLSource, UserMsg

from agentsentinel.__main__ import main
from agentsentinel.contracts import TaskState, TestStatus
from agentsentinel.governance import DenyAllSanitizer, MatrixPermissionPolicy, RegexSanitizer
from agentsentinel.pipeline import SentinelPipeline
from agentsentinel.roles import (
    AgentResult,
    Coder,
    Manager,
    Orchestrator,
    PipelineOutcome,
    RoleSpec,
    StubChatModel,
    Tester,
    UnknownToolError,
    WorkspaceOffloader,
    Writer,
    build_toolkit,
    decompose_task,
    run_agent_sync,
    text_reply,
)
from agentsentinel.tools import StubGitOps
from agentsentinel.workspace import Workspace

AUDIT_FIELDS = {
    "timestamp",
    "caller",
    "commit_hash",
    "scan_result",
    "test_result",
    "action_type",
    "reason",
}


class _AlwaysFailingTester(Tester):
    """固定返回 FAIL，用于触发连续失败回退。"""

    def handle(self, task: str, workspace: Workspace, *, feedback: str = "") -> AgentResult:
        return AgentResult(agent=self.name, summary="forced failure", status=TestStatus.FAIL)


def _read_audit(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _tool_names(agent: Agent) -> set[str]:
    return {tool.name for group in agent.toolkit.tool_groups for tool in group.tools}


def _stub_model() -> StubChatModel:
    return StubChatModel(lambda messages, tools: text_reply("ok"))


def test_model_driven_e2e_publishes_once_with_full_audit(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    git_ops = StubGitOps()

    result = SentinelPipeline(workspace, git_ops=git_ops).run("实现登录接口\n补充变更文档")

    assert result.status is PipelineOutcome.PASSED
    assert (workspace.code_dir / "main.py").is_file()
    assert (workspace.docs_dir / "CHANGELOG.md").is_file()
    assert list(workspace.reports_dir.glob("test-*.json"))
    assert [status.state for status in result.task_statuses] == [
        TaskState.PASSED,
        TaskState.PASSED,
    ]

    records = _read_audit(result.audit_log)
    assert len(records) == 1
    record = records[0]
    assert set(record) == AUDIT_FIELDS
    assert record["caller"] == "Orchestrator"
    assert record["scan_result"] == "ALLOW"
    assert record["test_result"] == "PASS"
    assert record["action_type"] == "push"
    assert record["reason"] == ""
    assert str(record["commit_hash"]).startswith("stub")

    task_snapshot = json.loads((workspace.reports_dir / "task-status.json").read_text("utf-8"))
    assert len(task_snapshot["tasks"]) == 2


def test_orchestrator_agent_binds_whitelist_and_offloader(tmp_path: Path) -> None:
    pipeline = SentinelPipeline(Workspace(tmp_path / "workspace"), git_ops=StubGitOps())

    pipeline.run("实现登录接口")

    agent = pipeline.orchestrator.last_agent
    assert isinstance(agent, Agent)
    assert _tool_names(agent) == set(Orchestrator.tools)
    assert not _tool_names(agent) & set(Orchestrator.spec.forbidden_tools)
    assert isinstance(agent.offloader, WorkspaceOffloader)


def test_role_agents_bind_toolkits_from_whitelist(tmp_path: Path) -> None:
    manager = Manager(
        workspace=Workspace(tmp_path / "workspace"),
        sanitizer=RegexSanitizer(),
        git_ops=StubGitOps(),
        policy=MatrixPermissionPolicy(),
    )

    for role in (Coder(), Tester(), Writer(), manager):
        agent = role.build_agent(_stub_model())
        names = _tool_names(agent)
        assert names == set(role.tools)
        assert not names & set(role.spec.forbidden_tools)


def test_coder_and_tester_agents_keep_separate_contexts() -> None:
    coder_agent = Coder().build_agent(_stub_model())
    tester_agent = Tester().build_agent(_stub_model())

    assert coder_agent.state is not tester_agent.state
    run_agent_sync(coder_agent, UserMsg("user", "写一个登录接口"))

    assert len(coder_agent.state.context) > 0
    assert len(tester_agent.state.context) == 0


def test_task_decomposition_splits_and_rejects_empty() -> None:
    subtasks = decompose_task("实现登录接口\n补充文档；加测试")

    assert [sub.task_id for sub in subtasks] == ["T1", "T2", "T3"]
    assert [sub.text for sub in subtasks] == ["实现登录接口", "补充文档", "加测试"]

    with pytest.raises(ValueError):
        decompose_task("   \n  ")


def test_unbound_tool_is_rejected() -> None:
    with pytest.raises(UnknownToolError):
        build_toolkit(RoleSpec(name="X", prompt_name="coder", tools=("not_bound",)))

    with pytest.raises(ValueError):
        build_toolkit(
            RoleSpec(
                name="X",
                prompt_name="coder",
                tools=("file_write",),
                forbidden_tools=("file_write",),
            )
        )


def test_offloader_persists_references_in_workspace(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    offloader = WorkspaceOffloader(workspace)

    reference = asyncio.run(offloader.offload_context("session-1", [UserMsg("user", "hello")]))

    assert reference == "workspace://reports/offload/context-session-1.json"
    assert (workspace.reports_dir / "offload" / "context-session-1.json").is_file()

    data_block = _data_block("abc")
    moved = asyncio.run(offloader.offload_data_block(data_block))
    assert isinstance(moved.source, URLSource)
    assert (workspace.reports_dir / "offload").is_dir()


def test_deny_is_terminal_and_never_reaches_git(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    git_ops = StubGitOps()

    result = SentinelPipeline(workspace, sanitizer=DenyAllSanitizer(), git_ops=git_ops).run(
        "实现登录接口"
    )

    assert result.status is PipelineOutcome.DENIED
    assert git_ops.calls == []
    records = _read_audit(result.audit_log)
    assert len(records) == 1
    assert records[0]["scan_result"] == "DENY"
    assert records[0]["action_type"] == "push"
    assert records[0]["commit_hash"] == ""
    assert str(records[0]["reason"]).startswith("SCAN_DENIED:")


def test_two_failures_revert_to_real_baseline(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    git_ops = StubGitOps()

    result = SentinelPipeline(
        workspace,
        git_ops=git_ops,
        baseline_ref="baseline-seed",
        tester=_AlwaysFailingTester(),
    ).run("实现登录接口")

    assert result.status is PipelineOutcome.FAILED
    assert result.attempts == 2
    assert ("reset_to_baseline", "baseline-seed") in git_ops.calls
    assert ("push", "") not in git_ops.calls
    record = _read_audit(result.audit_log)[-1]
    assert record["action_type"] == "revert"
    assert record["caller"] == "Orchestrator"
    assert record["scan_result"] == "NOT_RUN"
    assert record["reason"] == "SCAN_NOT_REQUIRED"


def test_revert_is_rejected_and_audited_for_non_orchestrator(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    git_ops = StubGitOps()
    pipeline = SentinelPipeline(workspace, git_ops=git_ops, baseline_ref="baseline-seed")

    with pytest.raises(PermissionError):
        pipeline.manager.revert(caller="Coder", ref="baseline-seed", test_status=TestStatus.FAIL)

    assert not any(call[0] == "reset_to_baseline" for call in git_ops.calls)
    record = _read_audit(pipeline.manager.audit_log_path)[-1]
    assert record["caller"] == "Coder"
    assert record["reason"] == "PERMISSION_DENIED"


def test_missing_baseline_blocks_revert_instead_of_using_head(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    git_ops = StubGitOps()

    result = SentinelPipeline(workspace, git_ops=git_ops, tester=_AlwaysFailingTester()).run(
        "实现登录接口"
    )

    assert result.status is PipelineOutcome.FAILED
    assert "基线" in result.summary
    assert not any(call[0] == "reset_to_baseline" for call in git_ops.calls)


def test_baseline_tag_from_previous_push_is_reused(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    git_ops = StubGitOps()
    pipeline = SentinelPipeline(workspace, git_ops=git_ops)

    first = pipeline.run("首次任务")
    assert first.status is PipelineOutcome.PASSED
    tag = pipeline.manager.last_baseline_tag
    assert tag is not None
    assert tag.startswith("baseline-")
    assert tag != "HEAD"
    assert ("tag_baseline", tag) in git_ops.calls

    failing = Orchestrator(
        coder=Coder(),
        tester=_AlwaysFailingTester(),
        writer=Writer(),
        manager=pipeline.manager,
        baseline_ref=None,
    )
    second = failing.run("第二个任务", workspace)

    assert second.status is PipelineOutcome.FAILED
    assert ("reset_to_baseline", tag) in git_ops.calls


def test_e2e_makes_no_external_network_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_connect = socket.socket.connect

    def _guard_connect(sock: socket.socket, address: object) -> None:
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError(f"network access attempted: {address}")
        original_connect(sock, address)  # type: ignore[arg-type]

    def _no_dns(*args: object, **kwargs: object) -> None:
        raise AssertionError("DNS lookup attempted")

    monkeypatch.setattr(socket.socket, "connect", _guard_connect)
    monkeypatch.setattr(socket, "create_connection", _no_dns)
    monkeypatch.setattr(socket, "getaddrinfo", _no_dns)

    result = SentinelPipeline(Workspace(tmp_path / "workspace"), git_ops=StubGitOps()).run(
        "实现登录接口"
    )

    assert result.status is PipelineOutcome.PASSED


def test_cli_runs_model_driven_pipeline_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["--stub", "--task", "实现登录接口\n补充变更文档"]) == 0

    snapshot = json.loads(
        (tmp_path / "workspace" / "reports" / "task-status.json").read_text(encoding="utf-8")
    )
    assert [task["state"] for task in snapshot["tasks"]] == ["passed", "passed"]
    assert (tmp_path / "workspace" / "audit" / "push-log.jsonl").is_file()


def _data_block(text: str) -> DataBlock:
    return DataBlock(
        id="block-1",
        source=Base64Source(
            data=base64.b64encode(text.encode()).decode(),
            media_type="text/plain",
        ),
    )
