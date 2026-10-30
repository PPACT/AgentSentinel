"""代码 Agent：只产出 workspace/code/ 下的代码，无 git 权限。"""

from agentscope.agent import Agent
from agentscope.model import ChatModelBase

from agentsentinel.roles.base import AgentResult, RoleSpec, build_role_agent
from agentsentinel.workspace import Workspace


class Coder:
    """离线行为保持确定性；M4 通过 build_agent 注入真实模型。"""

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
        return build_role_agent(self.spec, model)

    def handle(self, task: str, workspace: Workspace, *, feedback: str = "") -> AgentResult:
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
