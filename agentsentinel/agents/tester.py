"""测试 Agent 的 S1 确定性桩；真实测试执行见 M3。"""

from datetime import UTC, datetime

from agentsentinel.agents.base import AgentResult
from agentsentinel.contracts import TestReport, TestStatus
from agentsentinel.workspace import Workspace


class Tester:
    """读取代码产物并写出严格 JSON 测试报告，不轻信代码 Agent 自评。"""

    name: str = "Tester"
    tools: tuple[str, ...] = ("file_read", "bash")

    def handle(self, task: str, workspace: Workspace) -> AgentResult:
        code = workspace.read_text("code/main.py")
        passed = "def main" in code
        status = TestStatus.PASS if passed else TestStatus.FAIL
        report = TestReport(
            status=status,
            summary="stub check passed" if passed else "stub check failed: main missing",
            details="TODO(M3): 替换为真实测试执行与回归清单。",
            timestamp=datetime.now(UTC),
        )
        path = workspace.write_report(report)
        return AgentResult(
            agent=self.name,
            summary=report.summary,
            artifacts=(path,),
            status=status,
        )
