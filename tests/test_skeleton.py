"""S1 骨架：模块可导入、接口齐备、角色工具边界可见。"""

import importlib
import json
from pathlib import Path

import pytest

import agentsentinel
from agentsentinel.agents import (
    AgentRole,
    Coder,
    Manager,
    Orchestrator,
    Tester,
    Writer,
)
from agentsentinel.contracts import TestStatus
from agentsentinel.governance import (
    AllowAllPolicy,
    AllowAllSanitizer,
    MatrixPermissionPolicy,
)
from agentsentinel.governance.permissions import PermissionPolicy
from agentsentinel.governance.sanitizer import Sanitizer
from agentsentinel.pipeline import Pipeline
from agentsentinel.tools import GitOps, StubGitOps
from agentsentinel.workspace import Workspace

SKELETON_MODULES = (
    "agentsentinel.governance",
    "agentsentinel.governance.sanitizer",
    "agentsentinel.governance.permissions",
    "agentsentinel.tools",
    "agentsentinel.tools.git_ops",
    "agentsentinel.agents",
    "agentsentinel.agents.base",
    "agentsentinel.agents.orchestrator",
    "agentsentinel.agents.coder",
    "agentsentinel.agents.tester",
    "agentsentinel.agents.writer",
    "agentsentinel.agents.manager",
    "agentsentinel.pipeline",
    "agentsentinel.__main__",
)


@pytest.mark.parametrize("module_name", SKELETON_MODULES)
def test_skeleton_module_imports(module_name: str) -> None:
    assert importlib.import_module(module_name) is not None


def test_protocols_expose_expected_methods() -> None:
    protocols = {
        Sanitizer: ("scan",),
        PermissionPolicy: ("check",),
        GitOps: ("commit", "push", "tag_baseline", "reset_to_baseline"),
        AgentRole: ("handle",),
        Pipeline: ("run",),
    }
    for protocol, methods in protocols.items():
        for method in methods:
            assert callable(getattr(protocol, method))


def test_roles_publish_name_and_tools() -> None:
    for role in (Orchestrator, Coder, Tester, Writer, Manager):
        assert isinstance(role.name, str)
        assert role.tools


def test_code_and_test_roles_have_no_git_tools() -> None:
    for role in (Coder, Tester, Writer):
        assert not any("git" in tool for tool in role.tools)


def test_manager_owns_high_risk_tools() -> None:
    assert "sanitize_scan" in Manager.tools
    assert "git_reset_to_baseline" in Manager.tools


def test_prompt_placeholders_exist_and_are_marked() -> None:
    prompt_dir = Path(agentsentinel.__file__).parent / "prompts"
    for role in ("orchestrator", "coder", "tester", "writer", "manager"):
        prompt = prompt_dir / f"{role}.md"
        assert prompt.is_file()
        assert "TODO(M3)" in prompt.read_text(encoding="utf-8")


def test_stub_git_ops_only_records_calls() -> None:
    git_ops = StubGitOps()

    commit_hash = git_ops.commit("msg")
    git_ops.tag_baseline("baseline")
    git_ops.push()
    git_ops.reset_to_baseline("ref")

    assert commit_hash == "stub0000001"
    assert git_ops.calls == [
        ("commit", "msg"),
        ("tag_baseline", "baseline"),
        ("push", ""),
        ("reset_to_baseline", "ref"),
    ]


def _manager(
    workspace: Workspace,
    policy: PermissionPolicy | None = None,
) -> Manager:
    return Manager(
        workspace=workspace,
        sanitizer=AllowAllSanitizer(),
        git_ops=StubGitOps(),
        policy=policy if policy is not None else AllowAllPolicy(),
    )


def test_revert_rejects_non_orchestrator(tmp_path: Path) -> None:
    manager = _manager(Workspace(tmp_path / "workspace"))

    with pytest.raises(PermissionError):
        manager.revert("Coder", "baseline-ref", TestStatus.FAIL)


def test_revert_rejection_is_audited(tmp_path: Path) -> None:
    manager = _manager(Workspace(tmp_path / "workspace"))

    with pytest.raises(PermissionError):
        manager.revert("Coder", "baseline-ref", TestStatus.FAIL)

    records = [
        json.loads(line) for line in manager.audit_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["action_type"] == "revert"
    assert records[-1]["caller"] == "Coder"
    assert records[-1]["commit_hash"] == "baseline-ref"


def test_publish_denied_by_policy_is_audited(tmp_path: Path) -> None:
    manager = _manager(Workspace(tmp_path / "workspace"), MatrixPermissionPolicy())

    published = manager.publish(
        caller="Manager",
        commit_message="feat: demo",
        payload="clean content",
        test_status=TestStatus.PASS,
    )

    assert published is False
    records = [
        json.loads(line) for line in manager.audit_log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["action_type"] == "push"
    assert records[-1]["caller"] == "Manager"
