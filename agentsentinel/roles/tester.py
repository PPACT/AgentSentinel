"""测试 Agent：只读产物、独立判定并写严格 JSON 报告。

与 Coder 无引用关系，保持上下文隔离（规范 §2.3）。
真实模式首期不提供 shell：读取代码后必须用 ``submit_test_report`` 提交结论，
未提交报告一律按 ``FAIL`` 处理。
"""

from datetime import UTC, datetime

from agentscope.agent import Agent
from agentscope.message import UserMsg
from agentscope.model import ChatModelBase

from agentsentinel.contracts import TestReport, TestStatus
from agentsentinel.roles.base import AgentResult, RoleSpec, build_role_agent
from agentsentinel.roles.runtime import run_agent_sync
from agentsentinel.roles.toolkit import build_toolkit
from agentsentinel.roles.workspace_tools import WorkspaceToolbox, build_workspace_toolkit
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

    def new_toolbox(self, workspace: Workspace) -> WorkspaceToolbox:
        """真实模式的工具箱：只读工作区 + 提交报告，不提供 shell。"""
        return WorkspaceToolbox(workspace, can_submit_report=True)

    def build_workspace_agent(self, model: ChatModelBase, toolbox: WorkspaceToolbox) -> Agent:
        toolkit = build_workspace_toolkit(
            {
                "file_read": toolbox.file_read,
                "submit_test_report": toolbox.submit_test_report,
            }
        )
        return build_role_agent(self.spec, model, toolkit=toolkit)

    def handle(
        self,
        task: str,
        workspace: Workspace,
        *,
        feedback: str = "",
        model: ChatModelBase | None = None,
    ) -> AgentResult:
        if model is not None:
            return self._handle_with_model(task, workspace, feedback, model)

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

    def _handle_with_model(
        self,
        task: str,
        workspace: Workspace,
        feedback: str,
        model: ChatModelBase,
    ) -> AgentResult:
        toolbox = self.new_toolbox(workspace)
        agent = self.build_workspace_agent(model, toolbox)
        prompt = (
            f"任务：{task}\n\n"
            "请读取 workspace/code/ 下的代码，独立判断是否通过。"
            "首期没有 shell，必须使用 submit_test_report 提交 PASS 或 FAIL 报告。"
        )
        if feedback:
            prompt += f"\n\n上一轮失败反馈：{feedback}"
        reply = run_agent_sync(agent, UserMsg("user", prompt))
        reply_text = (reply.get_text_content() or "").strip()

        if toolbox.report_status is None:
            summary = (
                f"{reply_text}；未提交测试报告，按 FAIL 处理"
                if reply_text
                else "未提交测试报告，按 FAIL 处理"
            )
            return AgentResult(
                agent=self.name,
                summary=summary,
                artifacts=tuple(toolbox.artifacts),
                status=TestStatus.FAIL,
            )
        return AgentResult(
            agent=self.name,
            summary=reply_text or toolbox.report_summary,
            artifacts=tuple(toolbox.artifacts),
            status=toolbox.report_status,
        )
