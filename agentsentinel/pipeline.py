"""编排入口：八步流水线（规范 §4），默认由离线脚本模型驱动 AgentScope Agent。

AgentScope 2.0.8 没有 ``TeamPipeline``；可用编排原语是 ``Agent`` 与 ``GoalPipeline``。
本项目采用 Supervisor 模式：主编排 Agent 用工具调用发起动作，专家角色在
会话状态机内执行，产物通过共享 Workspace 传递。依据见交流区 M4 回执。
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agentscope.message import Msg
from agentscope.model import ChatModelBase, ChatResponse

from agentsentinel.contracts import TaskStatus
from agentsentinel.governance.permissions import MatrixPermissionPolicy, PermissionPolicy
from agentsentinel.governance.sanitizer import RegexSanitizer, Sanitizer
from agentsentinel.roles import (
    AgentRole,
    Coder,
    Manager,
    Orchestrator,
    PipelineOutcome,
    Tester,
    Writer,
)
from agentsentinel.roles.orchestrator import OrchestrationSession, SessionState
from agentsentinel.roles.runtime import StubChatModel, text_reply, tool_call
from agentsentinel.tools.git_ops import GitOps, StubGitOps
from agentsentinel.workspace import Workspace

MAX_SUPERVISOR_CALLS = 12


@dataclass(frozen=True)
class PipelineResult:
    status: PipelineOutcome
    summary: str
    artifacts: tuple[Path, ...]
    audit_log: Path
    task_statuses: tuple[TaskStatus, ...] = ()
    attempts: int = 1


class Pipeline(Protocol):
    def run(self, task: str) -> PipelineResult: ...


def supervisor_responder(
    session: OrchestrationSession,
) -> Callable[[list[Msg], Sequence[dict[str, Any]]], ChatResponse]:
    """离线主编排脚本：每一轮只根据会话状态发出下一步工具调用。"""
    calls = 0

    def respond(messages: list[Msg], tools: Sequence[dict[str, Any]]) -> ChatResponse:
        nonlocal calls
        calls += 1
        if calls > MAX_SUPERVISOR_CALLS:
            return text_reply("离线脚本达到调用上限，停止编排")

        state = session.state
        if state in {SessionState.CODING, SessionState.TEST_FAILED}:
            return tool_call(
                "TeamAssign",
                {"role": "Coder", "task": session.task, "feedback": session.feedback},
            )
        if state is SessionState.CODED:
            return tool_call("TeamAssign", {"role": "Tester", "task": session.task})
        if state is SessionState.TEST_PASSED:
            return tool_call("TeamAssign", {"role": "Writer", "task": session.task})
        if state is SessionState.DOC_READY:
            return tool_call(
                "request_publish",
                {"commit_message": f"feat: {session.task[:60]}"},
            )
        return text_reply(session.summary_text())

    return respond


def scripted_model(session: OrchestrationSession) -> ChatModelBase:
    """默认模型：不联网的脚本模型，驱动完整八步链路。"""
    return StubChatModel(supervisor_responder(session))


class SentinelPipeline:
    """按规范 §4 串起分派 → 编码 → 测试 → 修复/回退 → 文档 → 扫描 → 推送 → 审计。"""

    def __init__(
        self,
        workspace: Workspace | None = None,
        *,
        sanitizer: Sanitizer | None = None,
        policy: PermissionPolicy | None = None,
        git_ops: GitOps | None = None,
        baseline_ref: str | None = None,
        max_attempts: int = 2,
        model_factory: Callable[[OrchestrationSession], ChatModelBase] | None = None,
        coder: AgentRole | None = None,
        tester: AgentRole | None = None,
        writer: AgentRole | None = None,
    ) -> None:
        self._workspace = workspace if workspace is not None else Workspace()
        self._workspace.ensure()
        self._manager = Manager(
            workspace=self._workspace,
            sanitizer=sanitizer if sanitizer is not None else RegexSanitizer(),
            git_ops=git_ops if git_ops is not None else StubGitOps(),
            policy=policy if policy is not None else MatrixPermissionPolicy(),
        )
        self._orchestrator = Orchestrator(
            coder=coder if coder is not None else Coder(),
            tester=tester if tester is not None else Tester(),
            writer=writer if writer is not None else Writer(),
            manager=self._manager,
            max_attempts=max_attempts,
            baseline_ref=baseline_ref,
        )
        self._model_factory = model_factory

    @property
    def orchestrator(self) -> Orchestrator:
        return self._orchestrator

    @property
    def manager(self) -> Manager:
        return self._manager

    def run(self, task: str) -> PipelineResult:
        factory = self._model_factory if self._model_factory is not None else scripted_model
        outcome = self._orchestrator.run_with_model(task, self._workspace, factory)
        return PipelineResult(
            status=outcome.status,
            summary=outcome.summary,
            artifacts=outcome.artifacts,
            audit_log=self._manager.audit_log_path,
            task_statuses=outcome.task_statuses,
            attempts=outcome.attempts,
        )


# 兼容 S1/M2/M3 的既有调用点；实现已替换为模型驱动的 SentinelPipeline。
StubPipeline = SentinelPipeline
