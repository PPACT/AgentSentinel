"""主编排 Agent：拆解、分派、验收、失败重试与连续失败回退（规范 §2.1、§5）。

编排策略（重试上限、回退触发）写在代码里，不交给模型自由决定；
模型只通过工具发起「分派 / 查询 / 推送 / 回退」，越权或乱序都会在会话层被拒。
"""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from agentscope.agent import Agent
from agentscope.message import UserMsg
from agentscope.model import ChatModelBase

from agentsentinel.contracts import TaskState, TaskStatus, TestStatus
from agentsentinel.governance.permissions import ORCHESTRATOR_NAME
from agentsentinel.roles.base import (
    AgentResult,
    AgentRole,
    OrchestrationResult,
    PipelineOutcome,
    RoleSpec,
    build_role_agent,
)
from agentsentinel.roles.manager import Manager
from agentsentinel.roles.offload import WorkspaceOffloader
from agentsentinel.roles.runtime import run_agent_sync
from agentsentinel.roles.toolkit import build_toolkit
from agentsentinel.workspace import Workspace

TASK_STATUS_FILE = "reports/task-status.json"
_TASK_SPLIT = re.compile(r"[\n;；]+")


@dataclass(frozen=True)
class SubTask:
    """拆解后的一个子任务。"""

    task_id: str
    text: str


def decompose_task(task: str) -> tuple[SubTask, ...]:
    """按换行与中英文分号拆解任务；空任务直接拒绝。"""
    if not task.strip():
        raise ValueError("任务描述不能为空")
    parts = [part.strip() for part in _TASK_SPLIT.split(task) if part.strip()]
    return tuple(SubTask(f"T{index}", text) for index, text in enumerate(parts, start=1))


class TaskBoard:
    """主编排维护的任务状态板；快照落盘到 workspace/reports/task-status.json。"""

    def __init__(self, subtasks: Sequence[SubTask]) -> None:
        self._subtasks = tuple(subtasks)
        self._states: dict[str, TaskState] = {sub.task_id: TaskState.PENDING for sub in subtasks}
        self._details: dict[str, str] = {sub.task_id: "" for sub in subtasks}

    def update(self, state: TaskState, detail: str = "") -> None:
        for task_id in self._states:
            self._states[task_id] = state
            self._details[task_id] = detail

    def snapshot(self) -> tuple[TaskStatus, ...]:
        now = datetime.now(UTC)
        return tuple(
            TaskStatus(
                task_id=sub.task_id,
                updated_at=now,
                state=self._states[sub.task_id],
                detail=self._details[sub.task_id] or sub.text,
            )
            for sub in self._subtasks
        )

    def persist(self, workspace: Workspace) -> Path:
        payload = {"tasks": [status.model_dump(mode="json") for status in self.snapshot()]}
        return workspace.write_text(
            TASK_STATUS_FILE,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )


class SessionState(StrEnum):
    """八步流水线在会话内的状态。"""

    CODING = "coding"
    CODED = "coded"
    TEST_FAILED = "test_failed"
    TEST_PASSED = "test_passed"
    DOC_READY = "doc_ready"
    PUBLISHED = "published"
    DENIED = "denied"
    REVERTED = "reverted"
    BLOCKED = "blocked"


TERMINAL_STATES = frozenset(
    {
        SessionState.PUBLISHED,
        SessionState.DENIED,
        SessionState.REVERTED,
        SessionState.BLOCKED,
    }
)


class OrchestrationSession:
    """一次任务的编排状态机。

    只保留摘要与产物路径，不持有产物正文（规范 §2.1、§5 的上下文卸载）。
    """

    def __init__(
        self,
        task: str,
        workspace: Workspace,
        *,
        coder: AgentRole,
        tester: AgentRole,
        writer: AgentRole,
        manager: Manager,
        max_attempts: int,
        baseline_ref: str | None,
    ) -> None:
        self.task = task
        self.workspace = workspace
        self.state = SessionState.CODING
        self.attempts = 0
        self.feedback = ""
        self.artifacts: list[Path] = []
        self.board = TaskBoard(decompose_task(task))
        self.board.update(TaskState.IN_PROGRESS, "已拆解，等待编码")
        self._coder = coder
        self._tester = tester
        self._writer = writer
        self._manager = manager
        self._max_attempts = max_attempts
        self._baseline_ref = baseline_ref
        self._reverted_to: str | None = None
        self._blocked_reason = ""

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def assign(self, role: str, task: str = "", feedback: str = "") -> str:
        """分派给 Coder / Tester / Writer；乱序分派直接拒绝。"""
        if self.terminal:
            raise RuntimeError(f"会话已终止（{self.state.value}），不再接受分派")
        target = role.strip().casefold()
        if target == self._coder.name.casefold():
            return self._assign_coder(task or self.task, feedback)
        if target == self._tester.name.casefold():
            return self._assign_tester(task or self.task)
        if target == self._writer.name.casefold():
            return self._assign_writer(task or self.task)
        raise ValueError(f"未知或不可分派的角色: {role}")

    def publish(self, commit_message: str) -> str:
        """测试通过后才允许推送；DENY 为终态，不重试。"""
        if self.state is not SessionState.DOC_READY:
            raise RuntimeError(f"当前状态 {self.state.value} 不允许推送")
        published = self._manager.publish(
            caller=ORCHESTRATOR_NAME,
            commit_message=commit_message,
            test_status=TestStatus.PASS,
            artifacts=tuple(self.artifacts),
        )
        if not published:
            self.state = SessionState.DENIED
            return "推送被治理层阻断：DENY 为终态，不重试"
        self.state = SessionState.PUBLISHED
        return f"已推送；审计 {self._manager.audit_log_path}"

    def revert(self) -> str:
        """主编排发起回退；必须使用真实基线 tag。"""
        if self.terminal:
            raise RuntimeError(f"会话已终止（{self.state.value}），不能再次回退")
        ref = self._revert()
        return f"已回退到 {ref}"

    def summary_text(self) -> str:
        return self._summary_text()

    def status_payload(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "attempts": self.attempts,
            "terminal": self.terminal,
            "tasks": [status.model_dump(mode="json") for status in self.board.snapshot()],
            "artifacts": [str(path) for path in self.artifacts],
        }

    def result(self) -> OrchestrationResult:
        status_path = self.board.persist(self.workspace)
        if status_path not in self.artifacts:
            self.artifacts.append(status_path)
        outcome = {
            SessionState.PUBLISHED: PipelineOutcome.PASSED,
            SessionState.DENIED: PipelineOutcome.DENIED,
            SessionState.REVERTED: PipelineOutcome.FAILED,
        }.get(self.state, PipelineOutcome.FAILED)
        return OrchestrationResult(
            status=outcome,
            summary=self._summary_text(),
            artifacts=tuple(self.artifacts),
            attempts=max(self.attempts, 1),
            task_statuses=self.board.snapshot(),
        )

    def _assign_coder(self, task: str, feedback: str) -> str:
        if self.state not in {SessionState.CODING, SessionState.TEST_FAILED}:
            raise RuntimeError(f"当前状态 {self.state.value} 不允许分派 Coder")
        result = self._coder.handle(task, self.workspace, feedback=feedback)
        self.artifacts.extend(result.artifacts)
        self.state = SessionState.CODED
        self.board.update(TaskState.IN_PROGRESS, "编码完成，等待测试")
        return self._summary(result)

    def _assign_tester(self, task: str) -> str:
        if self.state is not SessionState.CODED:
            raise RuntimeError(f"当前状态 {self.state.value} 不允许分派 Tester")
        result = self._tester.handle(task, self.workspace, feedback=self.feedback)
        self.artifacts.extend(result.artifacts)
        if result.status is TestStatus.PASS:
            self.state = SessionState.TEST_PASSED
            self.board.update(TaskState.PASSED, "测试通过")
            return self._summary(result)

        self.attempts += 1
        self.feedback = result.summary
        if self.attempts >= self._max_attempts:
            try:
                ref = self._revert()
            except ValueError as exc:
                # 没有真实基线 tag 时不得静默回退到 HEAD：终止并如实报告。
                self.state = SessionState.BLOCKED
                self._blocked_reason = str(exc)
                self.board.update(TaskState.FAILED, str(exc))
                return f"{self._summary(result)}；回退失败：{exc}"
            return f"{self._summary(result)}；连续失败 {self.attempts} 次，已回退到 {ref}"
        self.state = SessionState.TEST_FAILED
        self.board.update(TaskState.FAILED, result.summary)
        return f"{self._summary(result)}；第 {self.attempts} 次失败，回传 Coder 局部修复"

    def _assign_writer(self, task: str) -> str:
        if self.state is not SessionState.TEST_PASSED:
            raise RuntimeError(f"当前状态 {self.state.value} 不允许分派 Writer")
        result = self._writer.handle(task, self.workspace, feedback=self.feedback)
        self.artifacts.extend(result.artifacts)
        self.state = SessionState.DOC_READY
        self.board.update(TaskState.PASSED, "文档已更新，等待推送")
        return self._summary(result)

    def _revert(self) -> str:
        ref = self._resolve_baseline()
        self._manager.revert(caller=ORCHESTRATOR_NAME, ref=ref, test_status=TestStatus.FAIL)
        self.state = SessionState.REVERTED
        self._reverted_to = ref
        self.board.update(TaskState.FAILED, f"连续失败，已回退到 {ref}")
        return ref

    def _resolve_baseline(self) -> str:
        ref = self._baseline_ref or self._manager.last_baseline_tag
        if not ref:
            raise ValueError("缺少真实基线 tag：未传入 baseline_ref，且本会话尚无成功推送")
        return ref

    def _summary(self, result: AgentResult) -> str:
        paths = ", ".join(str(path) for path in result.artifacts)
        return f"{result.agent}: {result.summary}" + (f"；产物 {paths}" if paths else "")

    def _summary_text(self) -> str:
        match self.state:
            case SessionState.PUBLISHED:
                return "pipeline completed"
            case SessionState.DENIED:
                return "push blocked by governance"
            case SessionState.REVERTED:
                return f"测试连续失败 {self.attempts} 次，已回退到 {self._reverted_to}"
            case SessionState.BLOCKED:
                return f"测试连续失败 {self.attempts} 次；{self._blocked_reason}"
            case _:
                return f"编排未完成，最终状态 {self.state.value}"


class Orchestrator:
    """只做分派与验收；不写码、不直接执行 git。"""

    name: str = "Orchestrator"
    tools: tuple[str, ...] = (
        "TeamAssign",
        "query_task_status",
        "request_publish",
        "request_revert",
    )
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
        baseline_ref: str | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts 必须至少为 1")
        self._coder = coder
        self._tester = tester
        self._writer = writer
        self._manager = manager
        self._max_attempts = max_attempts
        # 必须传入真实基线 tag；None 时回退会报错，绝不静默使用 HEAD。
        self._baseline_ref = baseline_ref
        self._active_session: OrchestrationSession | None = None
        self._last_agent: Agent | None = None

    @property
    def last_agent(self) -> Agent | None:
        """最近一次模型驱动编排使用的 AgentScope Agent（供测试核对绑定）。"""
        return self._last_agent

    def open_session(self, task: str, workspace: Workspace) -> OrchestrationSession:
        workspace.ensure()
        session = OrchestrationSession(
            task,
            workspace,
            coder=self._coder,
            tester=self._tester,
            writer=self._writer,
            manager=self._manager,
            max_attempts=self._max_attempts,
            baseline_ref=self._baseline_ref,
        )
        self._active_session = session
        return session

    def run(self, task: str, workspace: Workspace) -> OrchestrationResult:
        """确定性驱动八步流水线。"""
        session = self.open_session(task, workspace)
        for _ in range(self._max_attempts * 4 + 4):
            if session.terminal:
                return session.result()
            if session.state in {SessionState.CODING, SessionState.TEST_FAILED}:
                session.assign(self._coder.name, task, feedback=session.feedback)
            elif session.state is SessionState.CODED:
                session.assign(self._tester.name, task)
            elif session.state is SessionState.TEST_PASSED:
                session.assign(self._writer.name, task)
            elif session.state is SessionState.DOC_READY:
                session.publish(f"chore: {task[:60]}")
        raise RuntimeError("编排未在预期步数内结束")

    def run_with_model(
        self,
        task: str,
        workspace: Workspace,
        model_factory: Callable[[OrchestrationSession], ChatModelBase],
    ) -> OrchestrationResult:
        """用 AgentScope Agent 驱动同一条流水线；模型通过工具发起动作。"""
        session = self.open_session(task, workspace)
        agent = self._build_agent(model_factory(session), session)
        self._last_agent = agent
        run_agent_sync(agent, UserMsg("user", task))
        return session.result()

    def build_agent(self, model: ChatModelBase) -> Agent:
        if self._active_session is None:
            raise RuntimeError("Orchestrator 尚未开启会话：先调用 open_session")
        return self._build_agent(model, self._active_session)

    def _build_agent(self, model: ChatModelBase, session: OrchestrationSession) -> Agent:
        toolkit = build_toolkit(self.spec, extra_tools=self._session_extras(session))
        return build_role_agent(
            self.spec,
            model,
            toolkit=toolkit,
            offloader=WorkspaceOffloader(session.workspace),
        )

    def _session_extras(
        self,
        session: OrchestrationSession,
    ) -> Mapping[str, Callable[..., Any]]:
        def TeamAssign(role: str, task: str = "", feedback: str = "") -> str:
            """Dispatch a subtask to a team role (Coder, Tester or Writer).

            Args:
                role: Target role name.
                task: Task text; empty means the current task.
                feedback: Failure summary when re-dispatching the Coder.
            """
            return session.assign(role, task or session.task, feedback=feedback)

        def query_task_status() -> dict[str, Any]:
            """Return the current pipeline status snapshot."""
            return session.status_payload()

        def request_publish(commit_message: str) -> str:
            """Ask the Manager to scan, commit, tag baseline and push.

            Args:
                commit_message: Commit message for the pushed artifact set.
            """
            return session.publish(commit_message)

        def request_revert() -> str:
            """Ask the Manager to roll back to the latest baseline tag."""
            return session.revert()

        return {
            "TeamAssign": TeamAssign,
            "query_task_status": query_task_status,
            "request_publish": request_publish,
            "request_revert": request_revert,
        }
