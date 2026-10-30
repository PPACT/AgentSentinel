"""权限矩阵、BYPASS 禁令与 checkPermissions 阶段的扫描。"""

import pytest
from agentscope.permission import PermissionMode

from agentsentinel.contracts import ScanVerdict
from agentsentinel.governance.permissions import (
    MatrixPermissionPolicy,
    PermissionCheck,
    PermissionDecision,
    ensure_mode_allowed,
    preflight,
)
from agentsentinel.governance.sanitizer import DenyAllSanitizer, ScanResult


def test_force_push_is_always_denied() -> None:
    policy = MatrixPermissionPolicy()

    assert policy.check("git_push_force", "Orchestrator") is PermissionDecision.DENY
    assert policy.check("git_force_push", "Manager") is PermissionDecision.DENY


def test_reset_only_accepts_orchestrator() -> None:
    policy = MatrixPermissionPolicy()

    assert policy.check("git_reset_to_baseline", "Orchestrator") is PermissionDecision.ALLOW
    assert policy.check("git_reset_to_baseline", "Coder") is PermissionDecision.DENY
    assert policy.check("git_reset_to_baseline", "Manager") is PermissionDecision.DENY


def test_high_risk_tools_ask_for_manager_callers() -> None:
    policy = MatrixPermissionPolicy()

    for tool in ("git_commit", "git_push", "git_tag_baseline"):
        assert policy.check(tool, "Manager") is PermissionDecision.ASK
        assert policy.check(tool, "Orchestrator") is PermissionDecision.ALLOW


def test_regular_tools_are_allowed() -> None:
    policy = MatrixPermissionPolicy()

    assert policy.check("file_write", "Coder") is PermissionDecision.ALLOW


def test_bypass_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        ensure_mode_allowed(PermissionMode.BYPASS)

    for mode in (
        PermissionMode.DEFAULT,
        PermissionMode.ACCEPT_EDITS,
        PermissionMode.DONT_ASK,
        PermissionMode.EXPLORE,
    ):
        ensure_mode_allowed(mode)


def test_preflight_scans_payload_in_check_permissions_phase() -> None:
    policy = MatrixPermissionPolicy()

    result = preflight(policy, DenyAllSanitizer("命中规则"), "git_push", "Orchestrator", "x")

    assert result.decision is PermissionDecision.DENY
    assert result.reason == "命中规则"
    assert result.scan_result is ScanVerdict.DENY


def test_preflight_skips_scan_when_matrix_already_denies() -> None:
    class SpySanitizer:
        def __init__(self) -> None:
            self.calls = 0

        def scan(self, content: str) -> ScanResult:
            self.calls += 1
            return ScanResult(ScanVerdict.ALLOW)

    spy = SpySanitizer()
    result = preflight(MatrixPermissionPolicy(), spy, "git_reset_to_baseline", "Coder", "anything")

    assert result.decision is PermissionDecision.DENY
    assert spy.calls == 0
    assert isinstance(result, PermissionCheck)


def test_preflight_allows_clean_payload() -> None:
    from agentsentinel.governance.sanitizer import AllowAllSanitizer

    result = preflight(
        MatrixPermissionPolicy(), AllowAllSanitizer(), "git_push", "Orchestrator", ""
    )

    assert result.decision is PermissionDecision.ALLOW
    assert result.scan_result is ScanVerdict.ALLOW
