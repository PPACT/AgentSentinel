"""Agent 角色接口与通用结果类型。

S1 只固定接口；真实角色与 AgentScope 接入在 M3 落地。
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from agentsentinel.contracts import TestStatus
from agentsentinel.workspace import Workspace


class PipelineOutcome(StrEnum):
    """一次编排的最终结果。"""

    PASSED = "passed"
    FAILED = "failed"
    DENIED = "denied"


@dataclass(frozen=True)
class AgentResult:
    """角色执行结果；summary 用于回传主编排，避免携带大体积产物。"""

    agent: str
    summary: str
    artifacts: tuple[Path, ...] = ()
    status: TestStatus | None = None


@dataclass(frozen=True)
class OrchestrationResult:
    """主编排对一次任务的汇总结果。"""

    status: PipelineOutcome
    summary: str
    artifacts: tuple[Path, ...] = ()


class AgentRole(Protocol):
    """所有角色的统一接口。"""

    name: str
    tools: tuple[str, ...]

    def handle(self, task: str, workspace: Workspace) -> AgentResult: ...
