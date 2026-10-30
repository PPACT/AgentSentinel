"""五角色实现与组合接口；真实模型在 M4 注入。"""

from agentsentinel.roles.base import (
    AgentResult,
    AgentRole,
    OrchestrationResult,
    PipelineOutcome,
    RoleSpec,
    build_role_agent,
    load_prompt,
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
    "RoleSpec",
    "Tester",
    "Writer",
    "build_role_agent",
    "load_prompt",
]
