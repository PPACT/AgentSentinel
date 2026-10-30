"""编排入口与 S1 离线桩。

S1 用确定性桩跑通链路；AgentScope 真实编排见 M4。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agentsentinel.agents import (
    Coder,
    Manager,
    Orchestrator,
    PipelineOutcome,
    Tester,
    Writer,
)
from agentsentinel.governance.permissions import AllowAllPolicy, PermissionPolicy
from agentsentinel.governance.sanitizer import AllowAllSanitizer, Sanitizer
from agentsentinel.tools.git_ops import GitOps, StubGitOps
from agentsentinel.workspace import Workspace


@dataclass(frozen=True)
class PipelineResult:
    status: PipelineOutcome
    summary: str
    artifacts: tuple[Path, ...]
    audit_log: Path


class Pipeline(Protocol):
    def run(self, task: str) -> PipelineResult: ...


class StubPipeline:
    """离线编排：注入确定性桩，不联网、不调 LLM、不执行 git。

    TODO(M2): 换入真实 Sanitizer / PermissionPolicy / GitOps。
    TODO(M3): 换入真实五角色。
    TODO(M4): 换用 AgentScope 编排与上下文卸载。
    """

    def __init__(
        self,
        workspace: Workspace | None = None,
        *,
        sanitizer: Sanitizer | None = None,
        policy: PermissionPolicy | None = None,
        git_ops: GitOps | None = None,
    ) -> None:
        self._workspace = workspace if workspace is not None else Workspace()
        self._workspace.ensure()
        manager = Manager(
            workspace=self._workspace,
            sanitizer=sanitizer if sanitizer is not None else AllowAllSanitizer(),
            git_ops=git_ops if git_ops is not None else StubGitOps(),
            policy=policy if policy is not None else AllowAllPolicy(),
        )
        self._manager = manager
        self._orchestrator = Orchestrator(
            coder=Coder(),
            tester=Tester(),
            writer=Writer(),
            manager=manager,
        )

    def run(self, task: str) -> PipelineResult:
        outcome = self._orchestrator.run(task, self._workspace)
        return PipelineResult(
            status=outcome.status,
            summary=outcome.summary,
            artifacts=outcome.artifacts,
            audit_log=self._manager.audit_log_path,
        )
