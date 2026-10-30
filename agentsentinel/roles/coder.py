"""代码 Agent 的 S1 确定性桩；真实实现见 M3。"""

from agentsentinel.roles.base import AgentResult
from agentsentinel.workspace import Workspace


class Coder:
    """只往 workspace/code/ 写代码，不持有 git 工具。"""

    name: str = "Coder"
    tools: tuple[str, ...] = ("file_write", "file_edit")

    def handle(self, task: str, workspace: Workspace) -> AgentResult:
        content = f'"""离线桩产物；任务：{task[:80]}"""\n\ndef main() -> str:\n    return "ok"\n'
        path = workspace.write_text("code/main.py", content)
        return AgentResult(agent=self.name, summary="wrote code/main.py", artifacts=(path,))
