"""测试 Agent：只读产物、独立判定并写严格 JSON 报告。

与 Coder 无引用关系，保持上下文隔离（规范 §2.3）。
"""

from datetime import UTC, datetime

from agentscope.agent import Agent
from agentscope.model import ChatModelBase

from agentsentinel.contracts import TestReport, TestStatus
from agentsentinel.roles.base import AgentResult, RoleSpec, build_role_agent
from agentsentinel.roles.toolkit import build_toolkit
from agentsentinel.workspace import Workspace


class Tester:
    """默认假设代码有 Bug，不采信 Coder 自评。"""

    name: str = "Tester"
    tools: tuple[str, ...] = ("file_read", "bash")
    spec: RoleSpec = RoleSpec(
        name="Tester",
        prompt_name="tester",
        tools=tools,
        forbidden_tools=(
            "file_write",
            "file_edit",
            "git_commit",
            "git_push",
            "git_tag_baseline",
            "git_reset_to_baseline",
        ),
    )

    def build_agent(self, model: ChatModelBase) -> Agent:
        return build_role_agent(self.spec, model, toolkit=build_toolkit(self.spec))

    def handle(self, task: str, workspace: Workspace, *, feedback: str = "") -> AgentResult:
        code = workspace.read_text("code/main.py")
        passed = "def main" in code
        status = TestStatus.PASS if passed else TestStatus.FAIL
        report = TestReport(
            status=status,
            summary="stub check passed" if passed else "stub check failed: main missing",
            details="离线桩检查；M4 接入真实测试执行与回归清单。",
            timestamp=datetime.now(UTC),
        )
        path = workspace.write_report(report)
        return AgentResult(
            agent=self.name,
            summary=report.summary,
            artifacts=(path,),
            status=status,
        )
