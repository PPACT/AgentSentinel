"""五角色接口与 S1 桩实现；真实角色与 AgentScope 接入见 M3。"""

from agentsentinel.agents.base import (
    AgentResult,
    AgentRole,
    OrchestrationResult,
    PipelineOutcome,
)
from agentsentinel.agents.coder import Coder
from agentsentinel.agents.manager import Manager
from agentsentinel.agents.orchestrator import Orchestrator
from agentsentinel.agents.tester import Tester
from agentsentinel.agents.writer import Writer

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
