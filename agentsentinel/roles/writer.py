"""文档 Agent：只写 workspace/docs/，汇报摘要不超过 200 字。"""

from agentscope.agent import Agent
from agentscope.model import ChatModelBase

from agentsentinel.roles.base import AgentResult, RoleSpec, build_role_agent
from agentsentinel.roles.toolkit import build_toolkit
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

    def handle(self, task: str, workspace: Workspace, *, feedback: str = "") -> AgentResult:
        summary = task[:SUMMARY_LIMIT]
        path = workspace.write_text("docs/CHANGELOG.md", f"# 变更摘要\n\n{summary}\n")
        return AgentResult(agent=self.name, summary=summary, artifacts=(path,))
