"""Coder 权限边界与产物落点。"""

from pathlib import Path

from agentsentinel.roles import Coder
from agentsentinel.workspace import Workspace


def test_coder_writes_only_workspace_code(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")

    result = Coder().handle("实现登录接口", workspace)

    assert result.artifacts == (workspace.code_dir / "main.py",)
    assert (workspace.code_dir / "main.py").is_file()
    assert result.status is None


def test_coder_has_no_git_tools() -> None:
    assert not any(tool.startswith("git") for tool in Coder.tools)
    assert "git_push" in Coder.spec.forbidden_tools


def test_coder_uses_feedback_for_local_fix(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")

    Coder().handle("实现登录接口", workspace, feedback="main 缺失\n请补全")

    content = workspace.read_text("code/main.py")
    assert "修复反馈：main 缺失 请补全" in content
    assert "def main" in content
