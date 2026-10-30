"""主编排 Agent 的 S1 骨架：串起编码 → 测试 → 文档 → 治理发布。"""

from pathlib import Path

from agentsentinel.agents.base import (
    AgentRole,
    OrchestrationResult,
    PipelineOutcome,
)
from agentsentinel.agents.manager import Manager
from agentsentinel.contracts import TestStatus
from agentsentinel.workspace import Workspace


class Orchestrator:
    """只做分派与验收，不直接写码、不执行 git。"""

    name: str = "Orchestrator"
    tools: tuple[str, ...] = ("TeamAssign", "query_task_status")

    def __init__(
        self,
        coder: AgentRole,
        tester: AgentRole,
        writer: AgentRole,
        manager: Manager,
    ) -> None:
        self._coder = coder
        self._tester = tester
        self._writer = writer
        self._manager = manager

    def run(self, task: str, workspace: Workspace) -> OrchestrationResult:
        artifacts: list[Path] = []

        code = self._coder.handle(task, workspace)
        artifacts.extend(code.artifacts)

        tested = self._tester.handle(task, workspace)
        artifacts.extend(tested.artifacts)
        if tested.status is not TestStatus.PASS:
            return OrchestrationResult(
                status=PipelineOutcome.FAILED,
                summary=tested.summary,
                artifacts=tuple(artifacts),
            )

        doc = self._writer.handle(task, workspace)
        artifacts.extend(doc.artifacts)

        payload = "\n\n".join(path.read_text(encoding="utf-8") for path in artifacts)
        published = self._manager.publish(
            caller=self.name,
            commit_message=f"stub: {task[:60]}",
            payload=payload,
            test_status=TestStatus.PASS,
        )
        if not published:
            return OrchestrationResult(
                status=PipelineOutcome.DENIED,
                summary="push blocked by governance",
                artifacts=tuple(artifacts),
            )
        return OrchestrationResult(
            status=PipelineOutcome.PASSED,
            summary="pipeline completed",
            artifacts=tuple(artifacts),
        )
