"""五角色接口与 S1 桩实现；真实角色与 AgentScope 接入见 M3。"""

from agentsentinel.roles.base import (
    AgentResult,
    AgentRole,
    OrchestrationResult,
    PipelineOutcome,
)
from agentsentinel.roles.coder import Coder
from agentsentinel.roles.manager import Manager
from agentsentinel.roles.orchestrator import Orchestrator
from agentsentinel.roles.tester import Tester
from agentsentinel.roles.writer import Writer

__all__ = [
    "AgentResult",
    "AgentRole",
    "Coder",
    "Manager",
    "OrchestrationResult",
    "Orchestrator",
    "PipelineOutcome",
    "Tester",
    "Writer",
]
