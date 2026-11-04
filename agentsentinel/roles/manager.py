"""管理 Agent：扫描、推送、回退一律走治理接口并写审计。

所有审计集中在本类写入，避免同一操作产生重复 JSONL 记录。
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from agentscope.agent import Agent
from agentscope.model import ChatModelBase
from pydantic import ValidationError

from agentsentinel.audit import AuditLogger
from agentsentinel.contracts import ActionType, AuditRecord, ScanVerdict, TestStatus
from agentsentinel.governance.permissions import (
    ACTION_PUSH,
    ACTION_REVERT,
    ORCHESTRATOR_NAME,
    Mandate,
    PermissionDecision,
    PermissionPolicy,
    preflight,
    verify_mandate,
)
from agentsentinel.governance.sanitizer import Sanitizer
from agentsentinel.roles.base import RoleSpec, build_role_agent
from agentsentinel.roles.toolkit import build_toolkit
from agentsentinel.tools.git_ops import GitOps, ScanDeniedError
from agentsentinel.workspace import Workspace

AUDIT_LOG_NAME = "push-log.jsonl"
PERMISSION_DENIED_REASON = "PERMISSION_DENIED"
SCAN_DENIED_PREFIX = "SCAN_DENIED:"
REVERT_NO_SCAN_REASON = "SCAN_NOT_REQUIRED"


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
    spec: RoleSpec = RoleSpec(
        name="Manager",
        prompt_name="manager",
        tools=tools,
    )

    def build_agent(self, model: ChatModelBase) -> Agent:
        toolkit = build_toolkit(self.spec, extra_tools=self.toolkit_extras())
        return build_role_agent(self.spec, model, toolkit=toolkit)

    def __init__(
        self,
        workspace: Workspace,
        sanitizer: Sanitizer,
        git_ops: GitOps,
        policy: PermissionPolicy,
        *,
        target_branch: str = "main",
    ) -> None:
        self._workspace = workspace
        self._sanitizer = sanitizer
        self._git_ops = git_ops
        self._policy = policy
        self._target_branch = target_branch
        self._last_baseline_tag: str | None = None

    @property
    def audit_log_path(self) -> Path:
        return self._workspace.audit_dir / AUDIT_LOG_NAME

    @property
    def last_baseline_tag(self) -> str | None:
        """最近一次由本实例创建的基线 tag；供主编排回退时取真实 ref。"""
        return self._last_baseline_tag

    def publish(
        self,
        caller: str,
        commit_message: str,
        *,
        mandate: Mandate | None,
        payload: str = "",
        test_status: TestStatus = TestStatus.PASS,
        artifacts: Sequence[Path] = (),
    ) -> bool:
        """票据 + 权限 + 扫描 → 提交 → 打基线 → 推送；DENY 为终态，不重试。

        传入 ``artifacts`` 时由 Manager 从工作区读取内容用于扫描，
        主编排只需交出路径（上下文卸载）。
        """
        if artifacts:
            payload = "\n\n".join(path.read_text(encoding="utf-8") for path in artifacts)
        if (
            mandate is None
            or verify_mandate(
                mandate,
                caller=caller,
                action=ACTION_PUSH,
                target_branch=self._target_branch,
            )
            is not PermissionDecision.ALLOW
        ):
            self._write_audit(
                caller=caller,
                commit_hash="",
                scan_result=ScanVerdict.NOT_RUN,
                test_status=test_status,
                action=ActionType.PUSH,
                reason=PERMISSION_DENIED_REASON,
            )
            return False
        check = preflight(self._policy, self._sanitizer, "git_push", caller, payload)
        if check.decision is not PermissionDecision.ALLOW:
            if check.scan_result is ScanVerdict.NOT_RUN:
                scan_result = ScanVerdict.NOT_RUN
                reason = PERMISSION_DENIED_REASON
            else:
                scan_result = ScanVerdict.DENY
                reason = f"{SCAN_DENIED_PREFIX}{check.reason}"
            self._write_audit(
                caller=caller,
                commit_hash="",
                scan_result=scan_result,
                test_status=test_status,
                action=ActionType.PUSH,
                reason=reason,
            )
            return False

        commit_hash = self._git_ops.commit(commit_message, caller=caller)
        baseline_tag = f"baseline-{commit_hash}"
        self._git_ops.tag_baseline(baseline_tag, caller=caller)
        self._last_baseline_tag = baseline_tag
        try:
            self._git_ops.push(caller=caller, payload=payload, mandate=mandate)
        except ScanDeniedError as exc:
            # 工具层再次拦截；审计后终止，不重试。
            self._write_audit(
                caller=caller,
                commit_hash=commit_hash,
                scan_result=ScanVerdict.DENY,
                test_status=test_status,
                action=ActionType.PUSH,
                reason=f"{SCAN_DENIED_PREFIX}{exc}",
            )
            return False

        self._write_audit(
            caller=caller,
            commit_hash=commit_hash,
            scan_result=check.scan_result,
            test_status=test_status,
            action=ActionType.PUSH,
            reason="",
        )
        return True

    def revert(
        self,
        caller: str,
        ref: str,
        test_status: TestStatus,
        *,
        mandate: Mandate | None,
    ) -> None:
        """回退仅接受主编排凭有效票据调用；拒绝也写审计（规范 §5）。"""
        if (
            mandate is None
            or verify_mandate(
                mandate,
                caller=caller,
                action=ACTION_REVERT,
                target_branch=self._target_branch,
            )
            is not PermissionDecision.ALLOW
        ):
            self._write_audit(
                caller=caller,
                commit_hash=ref,
                scan_result=ScanVerdict.NOT_RUN,
                test_status=test_status,
                action=ActionType.REVERT,
                reason=PERMISSION_DENIED_REASON,
            )
            raise PermissionError(f"{caller} 的 Mandate 不足以执行回退")

        decision = self._policy.check("git_reset_to_baseline", caller)
        if caller != ORCHESTRATOR_NAME or decision is not PermissionDecision.ALLOW:
            self._write_audit(
                caller=caller,
                commit_hash=ref,
                scan_result=ScanVerdict.NOT_RUN,
                test_status=test_status,
                action=ActionType.REVERT,
                reason=PERMISSION_DENIED_REASON,
            )
            raise PermissionError(f"{caller} 无权执行回退")

        self._git_ops.reset_to_baseline(ref, caller=caller, mandate=mandate)
        # 回退不涉及内容扫描，如实记录 NOT_RUN。
        self._write_audit(
            caller=caller,
            commit_hash=ref,
            scan_result=ScanVerdict.NOT_RUN,
            test_status=test_status,
            action=ActionType.REVERT,
            reason=REVERT_NO_SCAN_REASON,
        )

    @staticmethod
    def _require_mandate(mandate: Mandate | None) -> Mandate:
        """模型工具入口收到的是 JSON 票据；缺失或字段非法一律按拒绝处理。"""
        try:
            return Mandate.model_validate(mandate)
        except ValidationError as exc:
            raise PermissionError("缺少有效 Mandate 票据") from exc

    def toolkit_extras(self) -> Mapping[str, Callable[..., object]]:
        """Manager 白名单工具的具体实现；高危工具仍受权限矩阵约束。"""

        def sanitize_scan(content: str) -> dict[str, str]:
            """Scan content for secrets and sensitive data.

            Args:
                content: Text to scan.
            """
            result = self._sanitizer.scan(content)
            return {"verdict": result.verdict.value, "reason": result.reason}

        def git_commit(message: str) -> str:
            """Commit workspace artifacts.

            Args:
                message: Commit message.
            """
            return self._git_ops.commit(message, caller=self.name)

        def git_push(payload: str = "", mandate: Mandate | None = None) -> str:
            """Push to the remote after a second scan.

            Args:
                payload: Text to scan before pushing.
                mandate: Mandate ticket (caller / allowed_action / target_branch).
            """
            self._git_ops.push(
                caller=self.name,
                payload=payload,
                mandate=self._require_mandate(mandate),
            )
            return "pushed"

        def git_tag_baseline(name: str) -> str:
            """Create the baseline tag used for rollback.

            Args:
                name: Tag name.
            """
            self._git_ops.tag_baseline(name, caller=self.name)
            return name

        def git_reset_to_baseline(ref: str, mandate: Mandate | None = None) -> str:
            """Roll back to a baseline tag. Orchestrator only.

            Args:
                ref: Baseline tag.
                mandate: Mandate ticket (caller / allowed_action / target_branch).
            """
            self._git_ops.reset_to_baseline(
                ref,
                caller=self.name,
                mandate=self._require_mandate(mandate),
            )
            return ref

        def audit_logger(
            caller: str,
            commit_hash: str,
            scan_result: str,
            test_result: str,
            action_type: str,
            reason: str = "",
        ) -> str:
            """Append one audit record to workspace/audit/push-log.jsonl.

            Args:
                caller: Calling role.
                commit_hash: Commit hash or baseline ref.
                scan_result: ALLOW / DENY / NOT_RUN.
                test_result: PASS / FAIL.
                action_type: push / revert.
                reason: Fixed reason code.
            """
            AuditLogger(self.audit_log_path).record(
                AuditRecord(
                    timestamp=datetime.now(UTC),
                    caller=caller,
                    commit_hash=commit_hash,
                    scan_result=ScanVerdict(scan_result),
                    test_result=TestStatus(test_result),
                    action_type=ActionType(action_type),
                    reason=reason,
                )
            )
            return str(self.audit_log_path)

        return {
            "sanitize_scan": sanitize_scan,
            "git_commit": git_commit,
            "git_push": git_push,
            "git_tag_baseline": git_tag_baseline,
            "git_reset_to_baseline": git_reset_to_baseline,
            "audit_logger": audit_logger,
        }

    def _write_audit(
        self,
        caller: str,
        commit_hash: str,
        scan_result: ScanVerdict,
        test_status: TestStatus,
        action: ActionType,
        reason: str,
    ) -> None:
        AuditLogger(self.audit_log_path).record(
            AuditRecord(
                timestamp=datetime.now(UTC),
                caller=caller,
                commit_hash=commit_hash,
                scan_result=scan_result,
                test_result=test_status,
                action_type=action,
                reason=reason,
            )
        )
