"""Manager 工具白名单、提示词与审计职责。"""

from pathlib import Path

from agentsentinel.contracts import ScanVerdict, TestStatus
from agentsentinel.governance import (
    ACTION_PUSH,
    Mandate,
    MatrixPermissionPolicy,
    RegexSanitizer,
)
from agentsentinel.roles import Manager
from agentsentinel.tools import StubGitOps
from agentsentinel.workspace import Workspace


def _manager(tmp_path: Path) -> Manager:
    return Manager(
        workspace=Workspace(tmp_path / "workspace"),
        sanitizer=RegexSanitizer(),
        git_ops=StubGitOps(),
        policy=MatrixPermissionPolicy(),
    )


def test_manager_owns_scan_push_and_revert(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    for tool in ("sanitize_scan", "git_push", "git_tag_baseline", "git_reset_to_baseline"):
        assert tool in manager.tools


def test_manager_uses_manager_prompt() -> None:
    prompt = Manager.spec.system_prompt()

    assert "Manager" in prompt
    assert "DENY" in prompt


def test_manager_blocks_secret_payload_before_git(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    sample_value = "sk-" + "a1" * 10  # secret-fixture: 假密钥

    published = manager.publish(
        caller="Orchestrator",
        commit_message="feat: demo",
        mandate=Mandate(
            caller="Orchestrator",
            allowed_action=ACTION_PUSH,
            target_branch="main",
        ),
        payload=f'key = "{sample_value}"',
        test_status=TestStatus.PASS,
    )

    assert published is False
    record = manager.audit_log_path.read_text(encoding="utf-8").splitlines()[-1]
    assert ScanVerdict.DENY.value in record
    assert "SCAN_DENIED:" in record
