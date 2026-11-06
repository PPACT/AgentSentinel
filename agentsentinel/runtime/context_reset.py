"""进程内上下文恢复：清空未压缩对话，用 checkpoint + 摘要重建前置上下文。

不重读项目文件；恢复文本只含 status / pending_action / artifacts 路径 /
audit_cursor，以及摘要正文（截断到固定行数）。
"""

from agentscope.agent import Agent
from agentscope.state import ReplyContext

from agentsentinel.contracts import Checkpoint, ContextSummary
from agentsentinel.workspace import Workspace

_MAX_SUMMARY_LINES = 35


class ContextResetter:
    def __init__(self, *, workspace: Workspace) -> None:
        self._workspace = workspace

    def build_resume_text(
        self,
        checkpoint: Checkpoint,
        summary: ContextSummary | None,
    ) -> str:
        summary_text = summary.summary if summary is not None else ""
        body = (summary_text or "(no summary)").splitlines()[:_MAX_SUMMARY_LINES]
        lines = [
            f"[resume] role={checkpoint.role} status={checkpoint.status.value}",
            f"[pending] {checkpoint.pending_action}",
            f"[artifacts] {'; '.join(checkpoint.artifacts)}",
            f"[audit_cursor] {checkpoint.audit_cursor}",
            "[summary]",
            *body,
        ]
        return "\n".join(lines)

    def reset(
        self,
        agent: Agent,
        checkpoint: Checkpoint,
        summary: ContextSummary | None = None,
    ) -> None:
        # 清空未压缩对话；权限 / 工具 / 任务板上下文保持不动。
        agent.state.context.clear()
        agent.state.summary = self.build_resume_text(checkpoint, summary)
        # 当前回合的临时状态属于被丢弃的那段对话，重置。
        agent.state.reply_context = ReplyContext()
