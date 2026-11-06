"""进程内 Agent 注册表与重启入口。

``restart`` 只在进程内恢复：读 checkpoint 与按角色分文件的摘要，清空
``agent.state.context`` 后把恢复文本写回 ``state.summary``，不重读项目。
"""

import logging
from dataclasses import dataclass

from agentscope.agent import Agent

from agentsentinel.contracts import Checkpoint, ContextSummary
from agentsentinel.runtime.checkpoint import CheckpointStore, ContextSummaryStore
from agentsentinel.runtime.context_reset import ContextResetter
from agentsentinel.workspace import Workspace

logger = logging.getLogger(__name__)


class CheckpointNotFound(LookupError):
    """重启时缺少 checkpoint；不静默新建。"""


@dataclass(frozen=True)
class RestartResult:
    checkpoint: Checkpoint
    summary: ContextSummary | None
    restarted: bool


def _role_key(role: str) -> str:
    return role.strip().casefold()


class RuntimeLauncher:
    """绑定 role -> Agent，并实现 ``HardThresholdHandler``。"""

    def __init__(
        self,
        *,
        workspace: Workspace,
        checkpoint_store: CheckpointStore | None = None,
        summary_store: ContextSummaryStore | None = None,
        resetter: ContextResetter | None = None,
    ) -> None:
        self._checkpoints = checkpoint_store or CheckpointStore(workspace)
        self._summaries = summary_store or ContextSummaryStore(workspace)
        self._resetter = resetter or ContextResetter(workspace=workspace)
        self._agents: dict[str, Agent] = {}

    def register(self, role: str, agent: Agent) -> None:
        key = _role_key(role)
        if key in self._agents:
            logger.warning("角色 %s 重复注册，进程内映射将被覆盖", key)
        self._agents[key] = agent

    def unregister(self, role: str) -> None:
        self._agents.pop(_role_key(role), None)

    def resume(self, role: str, agent: Agent) -> Checkpoint | None:
        checkpoint = self._checkpoints.load(role)
        if checkpoint is None:
            return None
        summary = self._summaries.load(role)
        self._resetter.reset(agent, checkpoint, summary)
        return checkpoint

    def restart(self, role: str) -> RestartResult:
        agent = self._agents.get(_role_key(role))
        if agent is None:
            raise RuntimeError(f"角色 {role!r} 未注册，无法重启")
        checkpoint = self._checkpoints.load(role)
        if checkpoint is None:
            raise CheckpointNotFound(f"角色 {role!r} 没有可用的 checkpoint")
        summary = self._summaries.load(role)
        self._resetter.reset(agent, checkpoint, summary)
        return RestartResult(checkpoint=checkpoint, summary=summary, restarted=True)

    def handle(self, checkpoint: Checkpoint, summary: ContextSummary | None) -> None:
        # 摘要已由 ContextMonitor 落盘，restart 会按角色重新读取。
        self.restart(checkpoint.role)
