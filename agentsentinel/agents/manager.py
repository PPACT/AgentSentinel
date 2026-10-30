"""管理 Agent：扫描、推送、回退一律走治理接口并写审计。

所有审计集中在本类写入，避免同一操作产生重复 JSONL 记录。
"""

from datetime import UTC, datetime
from pathlib import Path

from agentsentinel.audit import AuditLogger
from agentsentinel.contracts import ActionType, AuditRecord, ScanVerdict, TestStatus
from agentsentinel.governance.permissions import (
    ORCHESTRATOR_NAME,
    PermissionDecision,
    PermissionPolicy,
    preflight,
)
from agentsentinel.governance.sanitizer import Sanitizer
from agentsentinel.tools.git_ops import GitOps, ScanDeniedError
from agentsentinel.workspace import Workspace

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
        """权限 + 扫描 → 提交 → 打基线 → 推送；DENY 为终态，不重试。"""
        check = preflight(self._policy, self._sanitizer, "git_push", caller, payload)
        if check.decision is not PermissionDecision.ALLOW:
            self._write_audit(
                caller=caller,
                commit_hash="",
                scan_result=check.scan_result,
                test_status=test_status,
                action=ActionType.PUSH,
            )
            return False

        commit_hash = self._git_ops.commit(commit_message, caller=caller)
        self._git_ops.tag_baseline(f"baseline-{commit_hash}", caller=caller)
        try:
            self._git_ops.push(caller=caller, payload=payload)
        except ScanDeniedError:
            # 工具层再次拦截；审计后终止，不重试。
            self._write_audit(
                caller=caller,
                commit_hash=commit_hash,
                scan_result=ScanVerdict.DENY,
                test_status=test_status,
                action=ActionType.PUSH,
            )
            return False

        self._write_audit(
            caller=caller,
            commit_hash=commit_hash,
            scan_result=check.scan_result,
            test_status=test_status,
            action=ActionType.PUSH,
        )
        return True

    def revert(self, caller: str, ref: str, test_status: TestStatus) -> None:
        """回退仅接受主编排调用；拒绝也写审计（规范 §5）。"""
        decision = self._policy.check("git_reset_to_baseline", caller)
        if caller != ORCHESTRATOR_NAME or decision is not PermissionDecision.ALLOW:
            self._write_audit(
                caller=caller,
                commit_hash=ref,
                scan_result=ScanVerdict.ALLOW,
                test_status=test_status,
                action=ActionType.REVERT,
            )
            raise PermissionError(f"{caller} 无权执行回退")

        self._git_ops.reset_to_baseline(ref, caller=caller)
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
