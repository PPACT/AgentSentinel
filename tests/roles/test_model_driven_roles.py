"""P2-3 真实模式专家角色：工作区受限工具、结果转换与失败兜底。

全部用 ``StubChatModel`` 脚本化工具调用，不触网、不读真实 Key。
"""

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from agentscope.agent import Agent
from agentscope.message import Msg
from agentscope.model import ChatModelBase, ChatResponse

from agentsentinel.contracts import TestStatus
from agentsentinel.models import ModelRouter
from agentsentinel.pipeline import SentinelPipeline, supervisor_responder
from agentsentinel.roles import (
    Coder,
    PipelineOutcome,
    StubChatModel,
    Tester,
    Writer,
    text_reply,
    tool_call,
)
from agentsentinel.tools import StubGitOps
from agentsentinel.workspace import Workspace


def _sequence_model(*responses: Callable[[], ChatResponse]) -> StubChatModel:
    state = {"index": 0}

    def respond(messages: list[Msg], tools: Sequence[dict[str, Any]]) -> ChatResponse:
        index = min(state["index"], len(responses) - 1)
        state["index"] += 1
        return responses[index]()

    return StubChatModel(respond)


def _tool_names(agent: Agent) -> set[str]:
    return {tool.name for group in agent.toolkit.tool_groups for tool in group.tools}


def test_coder_model_writes_code_and_returns_artifact(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure()
    model = _sequence_model(
        lambda: tool_call(
            "file_write",
            {"path": "code/main.py", "content": "def main() -> str:\n    return 'ok'\n"},
        ),
        lambda: text_reply("wrote code/main.py"),
    )

    result = Coder().handle("实现登录接口", workspace, model=model)

    assert result.status is None
    assert result.summary == "wrote code/main.py"
    assert result.artifacts == (workspace.code_dir / "main.py",)
    assert "def main" in workspace.read_text("code/main.py")


def test_coder_model_without_file_write_is_failure(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure()
    model = _sequence_model(lambda: text_reply("done"))

    result = Coder().handle("实现登录接口", workspace, model=model)

    assert result.status is TestStatus.FAIL
    assert "未写入" in result.summary
    assert result.artifacts == ()


def test_coder_workspace_tools_reject_reads_and_writes_outside_code(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure()
    workspace.write_text("docs/notes.md", "notes")
    toolbox = Coder().new_toolbox(workspace)

    with pytest.raises(ValueError, match="越出 code"):
        toolbox.file_read("docs/notes.md")
    with pytest.raises(ValueError, match="越出 code"):
        toolbox.file_write("docs/notes.md", "changed")
    with pytest.raises(ValueError):
        toolbox.file_write("../escape.txt", "x")


def test_writer_model_writes_docs_and_truncates_summary(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure()
    model = _sequence_model(
        lambda: tool_call(
            "file_write",
            {"path": "docs/CHANGELOG.md", "content": "# 变更\n\n- 登录接口\n"},
        ),
        lambda: text_reply("x" * 300),
    )

    result = Writer().handle("补充文档", workspace, model=model)

    assert result.artifacts == (workspace.docs_dir / "CHANGELOG.md",)
    assert len(result.summary) == 200
    assert (workspace.docs_dir / "CHANGELOG.md").is_file()


def test_writer_workspace_tools_reject_code_writes(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure()
    toolbox = Writer().new_toolbox(workspace)

    with pytest.raises(ValueError, match="越出 docs"):
        toolbox.file_write("code/main.py", "def main(): pass")


def test_tester_model_submits_report_and_returns_status(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure()
    workspace.write_text("code/main.py", "def main() -> str:\n    return 'ok'\n")
    model = _sequence_model(
        lambda: tool_call("file_read", {"path": "code/main.py"}),
        lambda: tool_call(
            "submit_test_report",
            {"status": "PASS", "summary": "login ok", "details": "checked main"},
        ),
        lambda: text_reply("tests passed"),
    )

    result = Tester().handle("实现登录接口", workspace, model=model)

    assert result.status is TestStatus.PASS
    assert result.summary == "tests passed"
    reports = list(workspace.reports_dir.glob("test-*.json"))
    assert len(reports) == 1
    assert result.artifacts == (reports[0],)
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["summary"] == "login ok"


def test_tester_model_without_report_fails_closed(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure()
    model = _sequence_model(lambda: text_reply("looks fine"))

    result = Tester().handle("实现登录接口", workspace, model=model)

    assert result.status is TestStatus.FAIL
    assert "未提交测试报告" in result.summary
    assert result.artifacts == ()


def test_real_mode_agents_only_expose_workspace_tools(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure()
    stub = _sequence_model(lambda: text_reply("ok"))

    coder_agent = Coder().build_workspace_agent(stub, Coder().new_toolbox(workspace))
    tester_agent = Tester().build_workspace_agent(stub, Tester().new_toolbox(workspace))
    writer_agent = Writer().build_workspace_agent(stub, Writer().new_toolbox(workspace))

    assert _tool_names(coder_agent) == {"file_read", "file_write", "file_edit"}
    assert _tool_names(tester_agent) == {"file_read", "submit_test_report"}
    assert _tool_names(writer_agent) == {"file_read", "file_write", "file_edit"}
    for agent in (coder_agent, tester_agent, writer_agent):
        assert not _tool_names(agent) & {"bash", "Bash", "Write", "Edit"}


class _StubRoleRouter(ModelRouter):
    """只服务离线集成测试：按角色返回脚本模型，不构造真实 client。"""

    def __init__(self, models: Mapping[str, ChatModelBase | None]) -> None:
        self._models = dict(models)

    def for_role(self, role: str) -> ChatModelBase | None:
        return self._models.get(role.casefold())


def test_pipeline_real_backend_wires_router_for_experts_offline(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    git_ops = StubGitOps()
    router = _StubRoleRouter(
        {
            "coder": _sequence_model(
                lambda: tool_call(
                    "file_write",
                    {"path": "code/main.py", "content": "def main() -> str:\n    return 'ok'\n"},
                ),
                lambda: text_reply("wrote code/main.py"),
            ),
            "tester": _sequence_model(
                lambda: tool_call(
                    "submit_test_report",
                    {"status": "PASS", "summary": "ok", "details": ""},
                ),
                lambda: text_reply("tests passed"),
            ),
            "writer": _sequence_model(
                lambda: tool_call(
                    "file_write",
                    {"path": "docs/CHANGELOG.md", "content": "# 变更\n"},
                ),
                lambda: text_reply("docs updated"),
            ),
        }
    )
    pipeline = SentinelPipeline(
        workspace,
        git_ops=git_ops,
        model_backend="real",
        model_router=router,
        model_factory=lambda session: StubChatModel(supervisor_responder(session)),
    )

    result = pipeline.run("实现登录接口")

    assert result.status is PipelineOutcome.PASSED
    assert ("push", "") in git_ops.calls
    assert (workspace.code_dir / "main.py").is_file()
    assert (workspace.docs_dir / "CHANGELOG.md").is_file()
    assert list(workspace.reports_dir.glob("test-*.json"))
