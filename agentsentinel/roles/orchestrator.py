"""主编排 Agent：分派、验收、失败重试与连续失败回退（规范 §2.1、§5）。"""

from pathlib import Path

from agentscope.agent import Agent
from agentscope.model import ChatModelBase

from agentsentinel.contracts import TestStatus
from agentsentinel.roles.base import (
    AgentRole,
    OrchestrationResult,
    PipelineOutcome,
    RoleSpec,
    build_role_agent,
)
from agentsentinel.roles.manager import Manager
from agentsentinel.workspace import Workspace


class Orchestrator:
    """只做分派与验收；不写码、不直接执行 git。"""

    name: str = "Orchestrator"
    tools: tuple[str, ...] = ("TeamAssign", "query_task_status")
    spec: RoleSpec = RoleSpec(
        name="Orchestrator",
        prompt_name="orchestrator",
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

    def __init__(
        self,
        coder: AgentRole,
        tester: AgentRole,
        writer: AgentRole,
        manager: Manager,
        *,
        max_attempts: int = 2,
        baseline_ref: str = "HEAD",
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts 必须至少为 1")
        self._coder = coder
        self._tester = tester
        self._writer = writer
        self._manager = manager
        self._max_attempts = max_attempts
        # TODO(M4): 由真实的基线 tag 注入；默认 HEAD 仅作离线占位。
        self._baseline_ref = baseline_ref

    def build_agent(self, model: ChatModelBase) -> Agent:
        return build_role_agent(self.spec, model)

    def run(self, task: str, workspace: Workspace) -> OrchestrationResult:
        artifacts: list[Path] = []
        attempts = 0
        feedback = ""

        while attempts < self._max_attempts:
            attempts += 1
            code = self._coder.handle(task, workspace, feedback=feedback)
            artifacts.extend(code.artifacts)

            tested = self._tester.handle(task, workspace, feedback=feedback)
            artifacts.extend(tested.artifacts)
            if tested.status is TestStatus.PASS:
                break
            feedback = tested.summary
        else:
            # 连续失败达到上限：必须由主编排触发回退（规范 §2.1）。
            self._manager.revert(
                caller=self.name,
                ref=self._baseline_ref,
                test_status=TestStatus.FAIL,
            )
            return OrchestrationResult(
                status=PipelineOutcome.FAILED,
                summary=f"测试连续失败 {attempts} 次，已回退到 {self._baseline_ref}",
                artifacts=tuple(artifacts),
                attempts=attempts,
            )

        doc = self._writer.handle(task, workspace, feedback=feedback)
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
                attempts=attempts,
            )
        return OrchestrationResult(
            status=PipelineOutcome.PASSED,
            summary="pipeline completed",
            artifacts=tuple(artifacts),
            attempts=attempts,
        )
