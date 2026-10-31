"""权限矩阵与 checkPermissions 阶段（规范 §3.2、§5）。

常量口径以 AgentScope 2.0.8 的实际枚举为准：
- 模式：PermissionMode.{DEFAULT, ACCEPT_EDITS, EXPLORE, BYPASS, DONT_ASK}
- 行为：PermissionBehavior.{ALLOW, DENY, ASK, PASSTHROUGH}
本模块的 PermissionDecision 对应其中三者，声明禁用 BYPASS。
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from agentscope.permission import PermissionBehavior, PermissionMode

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


ROLE_PERMISSION_MODES: dict[str, PermissionMode] = {
    # 规范 §3.2：执行型角色用 ACCEPT_EDITS（工作区内编辑自动放行）。
    "Orchestrator": PermissionMode.ACCEPT_EDITS,
    "Coder": PermissionMode.ACCEPT_EDITS,
    "Tester": PermissionMode.ACCEPT_EDITS,
    "Writer": PermissionMode.ACCEPT_EDITS,
    # 管理角色离线无人值守：ASK 一律降级为 DENY，绝不静默放行。
    "Manager": PermissionMode.DONT_ASK,
}


def role_permission_mode(role: str) -> PermissionMode:
    """返回角色对应的 AgentScope 权限模式；未知角色退回最保守的 DEFAULT。"""
    return ROLE_PERMISSION_MODES.get(role, PermissionMode.DEFAULT)


def ensure_mode_allowed(mode: PermissionMode) -> None:
    """规范 §3.2：任何角色都不得使用 BYPASS 模式。"""
    if mode is PermissionMode.BYPASS:
        raise ValueError("禁止使用 BYPASS 权限模式")


def to_behavior(decision: PermissionDecision) -> PermissionBehavior:
    """项目判定映射到 AgentScope 权限行为。"""
    return {
        PermissionDecision.ALLOW: PermissionBehavior.ALLOW,
        PermissionDecision.DENY: PermissionBehavior.DENY,
        PermissionDecision.ASK: PermissionBehavior.ASK,
    }[decision]


def from_behavior(behavior: PermissionBehavior) -> PermissionDecision:
    """AgentScope 权限行为映射回项目判定；PASSTHROUGH 表示交给规则决定，按 ASK 处理。"""
    return {
        PermissionBehavior.ALLOW: PermissionDecision.ALLOW,
        PermissionBehavior.DENY: PermissionDecision.DENY,
        PermissionBehavior.ASK: PermissionDecision.ASK,
        PermissionBehavior.PASSTHROUGH: PermissionDecision.ASK,
    }[behavior]


def apply_mode(decision: PermissionDecision, mode: PermissionMode) -> PermissionDecision:
    """按权限模式收敛判定（M2 延后的模式映射，M4 落地）。

    - BYPASS：直接拒绝该模式本身；
    - DONT_ASK：无人值守，ASK 一律降级为 DENY；
    - 其余模式：判定不变（编辑放行由框架引擎按工作目录处理）。
    """
    ensure_mode_allowed(mode)
    if mode is PermissionMode.DONT_ASK and decision is PermissionDecision.ASK:
        return PermissionDecision.DENY
    return decision


@dataclass(frozen=True)
class PermissionCheck:
    """一次权限检查的完整结论，供调用方决定是否继续。

    未走到扫描步骤时 scan_result 必须为 NOT_RUN，不得伪造成 ALLOW。
    """

    decision: PermissionDecision
    reason: str = ""
    scan_result: ScanVerdict = ScanVerdict.NOT_RUN


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
            scan_result=ScanVerdict.NOT_RUN,
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
