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
from agentsentinel.roles.offload import WorkspaceOffloader
from agentsentinel.roles.orchestrator import (
    OrchestrationSession,
    Orchestrator,
    SessionState,
    SubTask,
    TaskBoard,
    decompose_task,
)
from agentsentinel.roles.runtime import StubChatModel, run_agent_sync, text_reply, tool_call
from agentsentinel.roles.tester import Tester
from agentsentinel.roles.toolkit import UnknownToolError, build_toolkit
from agentsentinel.roles.writer import Writer

__all__ = [
    "AgentResult",
    "AgentRole",
    "Coder",
    "Manager",
    "OrchestrationResult",
    "OrchestrationSession",
    "Orchestrator",
    "PipelineOutcome",
    "RoleSpec",
    "SessionState",
    "StubChatModel",
    "SubTask",
    "TaskBoard",
    "Tester",
    "UnknownToolError",
    "WorkspaceOffloader",
    "Writer",
    "build_role_agent",
    "build_toolkit",
    "decompose_task",
    "load_prompt",
    "run_agent_sync",
    "text_reply",
    "tool_call",
]
