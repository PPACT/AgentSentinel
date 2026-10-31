"""H1 危险 git 与推送前扫描的正反样本。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from tests.hooks.hook_utils import hook_output, run_hook


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Hook Test")
    _git(repo, "config", "user.email", "hook@example.com")
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "init")
    return repo


def _set_upstream(repo: Path) -> None:
    # 不执行真实 push：直接创建远端跟踪引用并通过 config 设置 upstream。
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "config", "branch.main.remote", "origin")
    _git(repo, "config", "branch.main.merge", "refs/heads/main")


def _bash(command: str, cwd: Path) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd),
    }


def _assert_allowed(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_git_add_all_is_denied() -> None:
    output = hook_output(run_hook("guard_git.py", _bash("git add -A", Path.cwd())))

    assert output["permissionDecision"] == "deny"


def test_git_add_dot_is_denied() -> None:
    output = hook_output(run_hook("guard_git.py", _bash("git add .", Path.cwd())))

    assert output["permissionDecision"] == "deny"


def test_explicit_git_add_is_allowed() -> None:
    result = run_hook("guard_git.py", _bash("git add README.md", Path.cwd()))

    _assert_allowed(result)


def test_force_push_is_denied() -> None:
    output = hook_output(
        run_hook("guard_git.py", _bash("git push --force origin main", Path.cwd()))
    )

    assert output["permissionDecision"] == "deny"


def test_delete_remote_is_denied() -> None:
    output = hook_output(run_hook("guard_git.py", _bash("git push origin :main", Path.cwd())))

    assert output["permissionDecision"] == "deny"


def test_reset_hard_is_denied() -> None:
    output = hook_output(run_hook("guard_git.py", _bash("git reset --hard HEAD~1", Path.cwd())))

    assert output["permissionDecision"] == "deny"


def test_clean_fdx_is_denied() -> None:
    output = hook_output(run_hook("guard_git.py", _bash("git clean -fdx", Path.cwd())))

    assert output["permissionDecision"] == "deny"


def test_push_with_stripe_fixture_is_denied(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    stripe_sample = "sk_" + "live_" + "a1" * 12
    (repo / "leak.py").write_text(f'key = "{stripe_sample}"\n', encoding="utf-8")
    _git(repo, "add", "leak.py")
    _git(repo, "commit", "-m", "add fixture")

    output = hook_output(
        run_hook("guard_git.py", _bash("git push origin main", repo), project_dir=repo)
    )

    assert output["permissionDecision"] == "deny"
    assert "脱敏扫描命中" in output["permissionDecisionReason"]


def test_clean_push_is_allowed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)

    result = run_hook("guard_git.py", _bash("git push origin main", repo), project_dir=repo)

    _assert_allowed(result)


def test_push_without_upstream_scans_head_and_allows_clean_tree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    result = run_hook("guard_git.py", _bash("git push origin main", repo), project_dir=repo)

    _assert_allowed(result)


def test_scan_failure_is_denied(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    result = run_hook(
        "guard_git.py",
        _bash("git push origin main", outside),
        project_dir=outside,
    )

    assert result.returncode == 2
    assert "扫描失败" in result.stderr


def test_non_git_command_is_allowed() -> None:
    result = run_hook("guard_git.py", _bash("uv run pytest -q", Path.cwd()))

    _assert_allowed(result)
