"""文档 Agent 的 S1 确定性桩；真实文档同步见 M3。"""

from agentsentinel.roles.base import AgentResult
from agentsentinel.workspace import Workspace

SUMMARY_LIMIT = 200


class Writer:
    """只往 workspace/docs/ 写文档，汇报摘要不超过 200 字。"""

    name: str = "Writer"
    tools: tuple[str, ...] = ("file_read", "file_write", "file_edit")

    def handle(self, task: str, workspace: Workspace) -> AgentResult:
        summary = task[:SUMMARY_LIMIT]
        path = workspace.write_text("docs/CHANGELOG.md", f"# 变更摘要\n\n{summary}\n")
        return AgentResult(agent=self.name, summary=summary, artifacts=(path,))
