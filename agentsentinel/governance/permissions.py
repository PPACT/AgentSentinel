"""权限矩阵与 checkPermissions 阶段（规范 §3.2、§5）。

常量口径以 AgentScope 2.0.8 的实际枚举为准：
- 模式：PermissionMode.{DEFAULT, ACCEPT_EDITS, EXPLORE, BYPASS, DONT_ASK}
- 行为：PermissionBehavior.{ALLOW, DENY, ASK, PASSTHROUGH}
本模块的 PermissionDecision 对应其中三者，声明禁用 BYPASS。
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from agentscope.permission import PermissionMode

from agentsentinel.contracts import ScanVerdict
from agentsentinel.governance.sanitizer import Sanitizer

ORCHESTRATOR_NAME = "Orchestrator"
RESET_TOOL = "git_reset_to_baseline"
HIGH_RISK_TOOLS = frozenset({"git_commit", "git_push", "git_tag_baseline"})
FORCE_PUSH_TOOLS = frozenset({"git_push_force", "git_force_push"})


class PermissionDecision(StrEnum):
    """项目权限判定；与 AgentScope PermissionBehavior 的 allow/deny/ask 对应。"""

    ALLOW = "ALLOW"
    DENY = "DENY"
    ASK = "ASK"


class PermissionPolicy(Protocol):
    def check(self, tool: str, caller: str) -> PermissionDecision: ...


class AllowAllPolicy:
    """放行所有调用，仅用于未接入治理的单元测试。"""

    def check(self, tool: str, caller: str) -> PermissionDecision:
        return PermissionDecision.ALLOW


class MatrixPermissionPolicy:
    """规范 §3.2 矩阵：高危工具需批准，force push 拒绝，回退仅主编排。"""

    def check(self, tool: str, caller: str) -> PermissionDecision:
        if tool in FORCE_PUSH_TOOLS:
            return PermissionDecision.DENY
        if tool == RESET_TOOL:
            return (
                PermissionDecision.ALLOW if caller == ORCHESTRATOR_NAME else PermissionDecision.DENY
            )
        if tool in HIGH_RISK_TOOLS:
            # Manager 自行发起高危操作需 ASK；主编排发起视为已批准。
            return (
                PermissionDecision.ALLOW if caller == ORCHESTRATOR_NAME else PermissionDecision.ASK
            )
        return PermissionDecision.ALLOW


def ensure_mode_allowed(mode: PermissionMode) -> None:
    """规范 §3.2：任何角色都不得使用 BYPASS 模式。"""
    if mode is PermissionMode.BYPASS:
        raise ValueError("禁止使用 BYPASS 权限模式")


@dataclass(frozen=True)
class PermissionCheck:
    """一次权限检查的完整结论，供调用方决定是否继续。"""

    decision: PermissionDecision
    reason: str = ""
    scan_result: ScanVerdict = ScanVerdict.ALLOW


def preflight(
    policy: PermissionPolicy,
    sanitizer: Sanitizer,
    tool: str,
    caller: str,
    payload: str,
) -> PermissionCheck:
    """对应框架的 checkPermissions 阶段：先判权限矩阵，再扫内容。"""
    decision = policy.check(tool, caller)
    if decision is not PermissionDecision.ALLOW:
        return PermissionCheck(
            decision=decision,
            reason=f"permission {decision.value}: {tool} for {caller}",
        )

    scan = sanitizer.scan(payload)
    if not scan.allowed:
        return PermissionCheck(
            decision=PermissionDecision.DENY,
            reason=scan.reason,
            scan_result=scan.verdict,
        )
    return PermissionCheck(
        decision=PermissionDecision.ALLOW,
        scan_result=scan.verdict,
    )
