"""文档 Agent：只写 workspace/docs/，汇报摘要不超过 200 字。"""

from agentscope.agent import Agent
from agentscope.message import UserMsg
from agentscope.model import ChatModelBase

from agentsentinel.roles.base import AgentResult, RoleSpec, build_role_agent
from agentsentinel.roles.runtime import run_agent_sync
from agentsentinel.roles.toolkit import build_toolkit
from agentsentinel.roles.workspace_tools import WorkspaceToolbox, build_workspace_toolkit
from agentsentinel.workspace import Workspace

SUMMARY_LIMIT = 200


class Writer:
    name: str = "Writer"
    tools: tuple[str, ...] = ("file_read", "file_write", "file_edit")
    spec: RoleSpec = RoleSpec(
        name="Writer",
        prompt_name="writer",
        tools=tools,
        forbidden_tools=(
            "git_commit",
            "git_push",
            "git_tag_baseline",
            "git_reset_to_baseline",
        ),
    )

    def build_agent(self, model: ChatModelBase) -> Agent:
        return build_role_agent(self.spec, model, toolkit=build_toolkit(self.spec))

    def new_toolbox(self, workspace: Workspace) -> WorkspaceToolbox:
        """真实模式的工具箱：全工作区只读，写范围限定 ``workspace/docs/``。"""
        return WorkspaceToolbox(workspace, write_scope="docs")

    def build_workspace_agent(self, model: ChatModelBase, toolbox: WorkspaceToolbox) -> Agent:
        toolkit = build_workspace_toolkit(
            {
                "file_read": toolbox.file_read,
                "file_write": toolbox.file_write,
                "file_edit": toolbox.file_edit,
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

        summary = task[:SUMMARY_LIMIT]
        path = workspace.write_text("docs/CHANGELOG.md", f"# 变更摘要\n\n{summary}\n")
        return AgentResult(agent=self.name, summary=summary, artifacts=(path,))

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
            "请读取 workspace/ 下的代码与测试报告，使用 file_write / file_edit "
            "更新 workspace/docs/ 下的文档；汇报不超过 200 字。"
        )
        if feedback:
            prompt += f"\n\n补充反馈：{feedback}"
        reply = run_agent_sync(agent, UserMsg("user", prompt))
        summary = (reply.get_text_content() or "").strip()[:SUMMARY_LIMIT]
        if not summary:
            summary = f"updated {len(toolbox.artifacts)} doc(s)"
        return AgentResult(agent=self.name, summary=summary, artifacts=tuple(toolbox.artifacts))
