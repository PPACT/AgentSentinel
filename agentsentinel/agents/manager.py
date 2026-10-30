"""管理 Agent 的 S1 骨架：扫描、推送、回退一律走接口与审计。

S1 不执行真实 git；权限矩阵与调用者鉴权的完整实现见 M2。
"""

from datetime import UTC, datetime
from pathlib import Path

from agentsentinel.audit import AuditLogger
from agentsentinel.contracts import ActionType, AuditRecord, ScanVerdict, TestStatus
from agentsentinel.governance.permissions import PermissionDecision, PermissionPolicy
from agentsentinel.governance.sanitizer import Sanitizer
from agentsentinel.tools.git_ops import GitOps
from agentsentinel.workspace import Workspace

ORCHESTRATOR_NAME = "Orchestrator"
AUDIT_LOG_NAME = "push-log.jsonl"


class Manager:
    """安全运维角色；所有高危动作都带审计。"""

    name: str = "Manager"
    tools: tuple[str, ...] = (
        "sanitize_scan",
        "git_commit",
        "git_push",
        "git_tag_baseline",
        "git_reset_to_baseline",
        "audit_logger",
    )

    def __init__(
        self,
        workspace: Workspace,
        sanitizer: Sanitizer,
        git_ops: GitOps,
        policy: PermissionPolicy,
    ) -> None:
        self._workspace = workspace
        self._sanitizer = sanitizer
        self._git_ops = git_ops
        self._policy = policy

    @property
    def audit_log_path(self) -> Path:
        return self._workspace.audit_dir / AUDIT_LOG_NAME

    def publish(
        self,
        caller: str,
        commit_message: str,
        payload: str,
        test_status: TestStatus,
    ) -> bool:
        """扫描 → 提交 → 打基线 → 推送；DENY 为终态，不重试。"""
        decision = self._policy.check("git_push", caller)
        if decision is not PermissionDecision.ALLOW:
            # TODO(M2): 权限拒绝的审计记录与 ASK 流程。
            return False

        scan = self._sanitizer.scan(payload)
        if not scan.allowed:
            # DENY 是业务终态：记录拦截后立即返回，不重试、不推送。
            self._write_audit(
                caller=caller,
                commit_hash="",
                scan_result=scan.verdict,
                test_status=test_status,
                action=ActionType.PUSH,
            )
            return False

        commit_hash = self._git_ops.commit(commit_message)
        self._git_ops.tag_baseline(f"baseline-{commit_hash}")
        self._git_ops.push()
        self._write_audit(
            caller=caller,
            commit_hash=commit_hash,
            scan_result=scan.verdict,
            test_status=test_status,
            action=ActionType.PUSH,
        )
        return True

    def revert(self, caller: str, ref: str, test_status: TestStatus) -> None:
        """回退仅接受主编排调用。TODO(M2): 权限拒绝时写审计。"""
        decision = self._policy.check("git_reset_to_baseline", caller)
        if caller != ORCHESTRATOR_NAME or decision is not PermissionDecision.ALLOW:
            raise PermissionError(f"{caller} 无权执行回退")

        self._git_ops.reset_to_baseline(ref)
        self._write_audit(
            caller=caller,
            commit_hash=ref,
            scan_result=ScanVerdict.ALLOW,
            test_status=test_status,
            action=ActionType.REVERT,
        )

    def _write_audit(
        self,
        caller: str,
        commit_hash: str,
        scan_result: ScanVerdict,
        test_status: TestStatus,
        action: ActionType,
    ) -> None:
        AuditLogger(self.audit_log_path).record(
            AuditRecord(
                timestamp=datetime.now(UTC),
                caller=caller,
                commit_hash=commit_hash,
                scan_result=scan_result,
                test_result=test_status,
                action_type=action,
            )
        )
