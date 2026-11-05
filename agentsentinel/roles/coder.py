"""代码 Agent：只产出 workspace/code/ 下的代码，无 git 权限。"""

from agentscope.agent import Agent
from agentscope.message import UserMsg
from agentscope.model import ChatModelBase

from agentsentinel.contracts import TestStatus
from agentsentinel.roles.base import AgentResult, RoleSpec, build_role_agent
from agentsentinel.roles.runtime import run_agent_sync
from agentsentinel.roles.toolkit import build_toolkit
from agentsentinel.roles.workspace_tools import WorkspaceToolbox, build_workspace_toolkit
from agentsentinel.workspace import Workspace


class Coder:
    """离线行为保持确定性；真实模式通过工作区受限工具写代码。"""

    name: str = "Coder"
    tools: tuple[str, ...] = ("file_write", "file_edit", "bash")
    spec: RoleSpec = RoleSpec(
        name="Coder",
        prompt_name="coder",
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
        """真实模式的工作区工具箱：读写都限定在 ``workspace/code/``。"""
        return WorkspaceToolbox(workspace, read_scope="code", write_scope="code")

    def build_workspace_agent(self, model: ChatModelBase, toolbox: WorkspaceToolbox) -> Agent:
        """真实模式 Agent：只绑定受限文件工具，不暴露 raw Bash / Write / Edit。"""
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

        fix_note = ""
        if feedback:
            fix_note = f"# 修复反馈：{feedback.replace(chr(10), ' ')[:80]}\n"
        content = (
            f'"""离线桩产物；任务：{task[:80]}"""\n'
            f"{fix_note}"
            "\n"
            "def main() -> str:\n"
            '    return "ok"\n'
        )
        path = workspace.write_text("code/main.py", content)
        return AgentResult(agent=self.name, summary="wrote code/main.py", artifacts=(path,))

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
            "只使用 file_read / file_write / file_edit 操作 workspace/code/ 下的代码。\n"
            "完成后用一句话汇报写了哪些文件与关键改动。"
        )
        if feedback:
            prompt += f"\n\n上一轮测试失败反馈：{feedback}\n请针对性修复。"
        reply = run_agent_sync(agent, UserMsg("user", prompt))
        if not toolbox.artifacts:
            return AgentResult(
                agent=self.name,
                summary="Coder 未写入任何文件，按失败处理",
                status=TestStatus.FAIL,
            )
        summary = (reply.get_text_content() or "").strip()
        if not summary:
            summary = f"wrote {len(toolbox.artifacts)} file(s)"
        return AgentResult(agent=self.name, summary=summary, artifacts=tuple(toolbox.artifacts))
