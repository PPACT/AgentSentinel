"""编排入口：八步流水线（规范 §4），默认由离线脚本模型驱动 AgentScope Agent。

AgentScope 2.0.8 没有 ``TeamPipeline``；可用编排原语是 ``Agent`` 与 ``GoalPipeline``。
本项目采用 Supervisor 模式：主编排 Agent 用工具调用发起动作，专家角色在
会话状态机内执行，产物通过共享 Workspace 传递。依据见交流区 M4 回执。
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from agentscope.message import Msg
from agentscope.model import ChatModelBase, ChatResponse

from agentsentinel.config import Settings
from agentsentinel.contracts import TaskStatus
from agentsentinel.governance.permissions import MatrixPermissionPolicy, PermissionPolicy
from agentsentinel.governance.sanitizer import RegexSanitizer, Sanitizer
from agentsentinel.models import ModelRouter
from agentsentinel.models.router import ROLE_TIERS
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
from agentsentinel.state import StateLogger
from agentsentinel.tools.git_ops import GitOps, StubGitOps
from agentsentinel.usage import BudgetExceededError, MeteredChatModel, UsageLedger
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
    """按规范 §4 串起分派 → 编码 → 测试 → 修复/回退 → 文档 → 扫描 → 推送 → 审计。

    默认 ``model_backend="stub"``：脚本模型驱动，零网络、行为与既有测试一致。
    ``real`` 模式按 ``ModelRouter`` 逐角色下发真实模型；专家角色只拿工作区受限工具。
    """

    def __init__(
        self,
        workspace: Workspace | None = None,
        *,
        sanitizer: Sanitizer | None = None,
        policy: PermissionPolicy | None = None,
        git_ops: GitOps | None = None,
        baseline_ref: str | None = None,
        max_attempts: int = 2,
        target_branch: str = "main",
        model_factory: Callable[[OrchestrationSession], ChatModelBase] | None = None,
        coder: AgentRole | None = None,
        tester: AgentRole | None = None,
        writer: AgentRole | None = None,
        model_backend: Literal["stub", "real"] = "stub",
        model_router: ModelRouter | None = None,
        settings: Settings | None = None,
    ) -> None:
        if model_backend not in {"stub", "real"}:
            raise ValueError(f"未知 model_backend: {model_backend}")
        self._model_backend = model_backend
        self._settings: Settings | None = settings if model_backend == "real" else None
        if model_backend == "real":
            if self._settings is None:
                self._settings = Settings()
            if model_router is None:
                model_router = ModelRouter(self._settings)
        self._model_router = model_router
        self._usage_ledger: UsageLedger | None = None
        self._workspace = workspace if workspace is not None else Workspace()
        self._workspace.ensure()
        self._manager = Manager(
            workspace=self._workspace,
            sanitizer=sanitizer if sanitizer is not None else RegexSanitizer(),
            git_ops=git_ops if git_ops is not None else StubGitOps(),
            policy=policy if policy is not None else MatrixPermissionPolicy(),
            target_branch=target_branch,
        )
        self._orchestrator = Orchestrator(
            coder=coder if coder is not None else Coder(),
            tester=tester if tester is not None else Tester(),
            writer=writer if writer is not None else Writer(),
            manager=self._manager,
            max_attempts=max_attempts,
            baseline_ref=baseline_ref,
            target_branch=target_branch,
            state_logger=StateLogger(self._workspace.pipeline_state_path),
            role_model_for=(
                self._role_model_for
                if model_backend == "real" and model_router is not None
                else None
            ),
        )
        self._model_factory = model_factory

    @property
    def orchestrator(self) -> Orchestrator:
        return self._orchestrator

    @property
    def manager(self) -> Manager:
        return self._manager

    def run(self, task: str) -> PipelineResult:
        self._usage_ledger = (
            UsageLedger(self._settings, self._workspace.usage_path)
            if self._model_backend == "real" and self._settings is not None
            else None
        )
        try:
            outcome = self._orchestrator.run_with_model(
                task, self._workspace, self._resolve_model_factory()
            )
        except BudgetExceededError as exc:
            return PipelineResult(
                status=PipelineOutcome.FAILED,
                summary=f"预算熔断：{exc}",
                artifacts=(),
                audit_log=self._manager.audit_log_path,
            )
        finally:
            self._usage_ledger = None
        return PipelineResult(
            status=outcome.status,
            summary=outcome.summary,
            artifacts=outcome.artifacts,
            audit_log=self._manager.audit_log_path,
            task_statuses=outcome.task_statuses,
            attempts=outcome.attempts,
        )

    def _role_model_for(self, role: str) -> ChatModelBase | None:
        """真实模式下按角色取模型并套上计量层；未开启运行时不计量。"""
        if self._model_router is None:
            return None
        model = self._model_router.for_role(role)
        if model is None:
            return None
        return self._metered_model(model, role)

    def _metered_model(self, model: ChatModelBase, role: str) -> ChatModelBase:
        ledger = self._usage_ledger
        if ledger is None:
            return model
        tier = ROLE_TIERS.get(role.casefold())
        if tier is None:
            return model
        return MeteredChatModel(model, role=role.casefold(), tier=tier, ledger=ledger)

    def _resolve_model_factory(self) -> Callable[[OrchestrationSession], ChatModelBase]:
        """显式 model_factory > real 模式的 router > 默认脚本模型。"""
        if self._model_factory is not None:
            return self._model_factory
        if self._model_backend == "real" and self._model_router is not None:
            router = self._model_router

            def orchestrator_factory(session: OrchestrationSession) -> ChatModelBase:
                model = router.for_role("orchestrator")
                if model is None:
                    raise RuntimeError("real 模式缺少 orchestrator 模型")
                return self._metered_model(model, "orchestrator")

            return orchestrator_factory
        return scripted_model


# 兼容 S1/M2/M3 的既有调用点；实现已替换为模型驱动的 SentinelPipeline。
StubPipeline = SentinelPipeline
