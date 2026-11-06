"""H1 不可逆命令拦截与推送前审查的正反样本。"""

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


def _stripe_sample() -> str:
    return "sk_" + "live_" + "a1" * 12


def test_git_add_all_is_denied() -> None:
    output = hook_output(run_hook("guard_git.py", _bash("git add -A", Path.cwd())))

    assert output["permissionDecision"] == "deny"


def test_git_add_combined_all_flag_is_denied() -> None:
    for command in ("git add -Av", "git add -vA"):
        output = hook_output(run_hook("guard_git.py", _bash(command, Path.cwd())))
        assert output["permissionDecision"] == "deny", command


def test_git_add_verbose_with_explicit_file_is_allowed() -> None:
    _assert_allowed(run_hook("guard_git.py", _bash("git add -v app.py", Path.cwd())))


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


def test_rm_rf_is_denied() -> None:
    output = hook_output(run_hook("guard_git.py", _bash("rm -rf build", Path.cwd())))

    assert output["permissionDecision"] == "deny"


def test_rm_force_single_file_is_allowed() -> None:
    result = run_hook("guard_git.py", _bash("rm -f stale.log", Path.cwd()))

    _assert_allowed(result)


def test_git_clean_force_delete_combinations_are_denied() -> None:
    for command in ("git clean -fd", "git clean -dfx", "git clean -fx"):
        output = hook_output(run_hook("guard_git.py", _bash(command, Path.cwd())))
        assert output["permissionDecision"] == "deny", command


def test_git_clean_dry_run_is_allowed() -> None:
    _assert_allowed(run_hook("guard_git.py", _bash("git clean -n", Path.cwd())))


def test_git_clean_long_force_with_dirs_or_excludes_is_denied() -> None:
    for command in ("git clean --force -d", "git clean --force -x"):
        output = hook_output(run_hook("guard_git.py", _bash(command, Path.cwd())))
        assert output["permissionDecision"] == "deny", command


def test_git_clean_dry_run_overrides_force() -> None:
    for command in ("git clean -ndf", "git clean --dry-run --force -d"):
        _assert_allowed(run_hook("guard_git.py", _bash(command, Path.cwd())))


def test_clean_push_is_allowed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)

    result = run_hook("guard_git.py", _bash("git push origin main", repo), project_dir=repo)

    _assert_allowed(result)


def test_push_with_secret_asks(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    (repo / "leak.py").write_text(f'key = "{_stripe_sample()}"\n', encoding="utf-8")
    _git(repo, "add", "leak.py")
    _git(repo, "commit", "-m", "add fixture")

    output = hook_output(
        run_hook("guard_git.py", _bash("git push origin main", repo), project_dir=repo)
    )

    assert output["permissionDecision"] == "ask"
    assert "HEAD:leak.py" in output["permissionDecisionReason"]


def test_push_scans_head_content_even_if_worktree_is_cleaned(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    leak = repo / "leak.py"
    leak.write_text(f'key = "{_stripe_sample()}"\n', encoding="utf-8")
    _git(repo, "add", "leak.py")
    _git(repo, "commit", "-m", "add fixture")
    leak.write_text("print('cleaned')\n", encoding="utf-8")

    output = hook_output(
        run_hook("guard_git.py", _bash("git push origin main", repo), project_dir=repo)
    )

    assert output["permissionDecision"] == "ask"
    assert "HEAD:leak.py" in output["permissionDecisionReason"]


def test_push_ignores_test_fixtures(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    (repo / "tests").mkdir()
    (repo / "tests" / "leak_fixture.py").write_text(
        f'key = "{_stripe_sample()}"\n', encoding="utf-8"
    )
    _git(repo, "add", "tests/leak_fixture.py")
    _git(repo, "commit", "-m", "add test fixture")

    result = run_hook("guard_git.py", _bash("git push origin main", repo), project_dir=repo)

    _assert_allowed(result)


def test_explicit_refspec_scans_pushed_branch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    _git(repo, "checkout", "-b", "leak")
    (repo / "leak.py").write_text(f'key = "{_stripe_sample()}"\n', encoding="utf-8")
    _git(repo, "add", "leak.py")
    _git(repo, "commit", "-m", "add fixture")
    _git(repo, "checkout", "main")

    output = hook_output(
        run_hook("guard_git.py", _bash("git push origin leak", repo), project_dir=repo)
    )

    assert output["permissionDecision"] == "ask"
    assert "leak.py" in output["permissionDecisionReason"]


def test_explicit_refspec_clean_branch_is_allowed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    _git(repo, "branch", "clean")

    result = run_hook("guard_git.py", _bash("git push origin clean", repo), project_dir=repo)

    _assert_allowed(result)


def test_colon_refspec_scans_against_remote_tracking(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    _git(repo, "update-ref", "refs/remotes/origin/release", "HEAD")
    _git(repo, "checkout", "-b", "feature")
    (repo / "leak.py").write_text(f'key = "{_stripe_sample()}"\n', encoding="utf-8")
    _git(repo, "add", "leak.py")
    _git(repo, "commit", "-m", "add fixture")
    _git(repo, "checkout", "main")

    output = hook_output(
        run_hook("guard_git.py", _bash("git push origin feature:release", repo), project_dir=repo)
    )

    assert output["permissionDecision"] == "ask"
    assert "leak.py" in output["permissionDecisionReason"]


def test_dirty_worktree_is_ignored_on_push(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    (repo / "app.py").write_text(f'key = "{_stripe_sample()}"\n', encoding="utf-8")

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


def test_quoted_dangerous_text_is_allowed() -> None:
    result = run_hook("guard_git.py", _bash('echo "git push -f"', Path.cwd()))

    _assert_allowed(result)


def test_commit_message_with_push_does_not_trigger_scan(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    (repo / "leak.py").write_text(f'key = "{_stripe_sample()}"\n', encoding="utf-8")
    _git(repo, "add", "leak.py")
    _git(repo, "commit", "-m", "add fixture")

    result = run_hook("guard_git.py", _bash("git commit -m push", repo), project_dir=repo)

    _assert_allowed(result)


def test_log_grep_push_does_not_trigger_scan(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    (repo / "leak.py").write_text(f'key = "{_stripe_sample()}"\n', encoding="utf-8")
    _git(repo, "add", "leak.py")
    _git(repo, "commit", "-m", "add fixture")

    result = run_hook("guard_git.py", _bash("git log --grep push", repo), project_dir=repo)

    _assert_allowed(result)


def test_push_with_global_option_still_scans(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    (repo / "leak.py").write_text(f'key = "{_stripe_sample()}"\n', encoding="utf-8")
    _git(repo, "add", "leak.py")
    _git(repo, "commit", "-m", "add fixture")

    output = hook_output(
        run_hook(
            "guard_git.py",
            _bash("git -c core.quotepath=false push origin main", repo),
            project_dir=repo,
        )
    )

    assert output["permissionDecision"] == "ask"


def test_push_with_redirection_still_scans(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    (repo / "leak.py").write_text(f'key = "{_stripe_sample()}"\n', encoding="utf-8")
    _git(repo, "add", "leak.py")
    _git(repo, "commit", "-m", "add fixture")

    output = hook_output(
        run_hook(
            "guard_git.py",
            _bash("git push origin main 2>&1 | tail -5", repo),
            project_dir=repo,
        )
    )

    assert output["permissionDecision"] == "ask"


def test_multiple_push_segments_scan_all_push_ranges(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    _git(repo, "branch", "clean")
    _git(repo, "checkout", "-b", "leak")
    (repo / "leak.py").write_text(f'key = "{_stripe_sample()}"\n', encoding="utf-8")
    _git(repo, "add", "leak.py")
    _git(repo, "commit", "-m", "add fixture")
    _git(repo, "checkout", "main")

    output = hook_output(
        run_hook(
            "guard_git.py",
            _bash("git push origin clean && git push origin leak", repo),
            project_dir=repo,
        )
    )

    assert output["permissionDecision"] == "ask"
    assert "leak.py" in output["permissionDecisionReason"]


def test_multiple_clean_push_segments_are_allowed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)
    _git(repo, "branch", "clean")

    result = run_hook(
        "guard_git.py",
        _bash("git push origin clean && git push origin main", repo),
        project_dir=repo,
    )

    _assert_allowed(result)


def test_clean_push_with_redirection_is_allowed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)

    result = run_hook(
        "guard_git.py",
        _bash("git push origin main > /dev/null 2>&1", repo),
        project_dir=repo,
    )

    _assert_allowed(result)


def test_clean_push_with_pipe_and_redirection_is_allowed(tmp_path: Path) -> None:
    # 重定向 token（2>&1）必须保持完整：被拆成 2> / & / 1 会把 1 当作 refspec。
    repo = _make_repo(tmp_path)
    _set_upstream(repo)

    result = run_hook(
        "guard_git.py",
        _bash("git push origin main 2>&1 | tee log.txt", repo),
        project_dir=repo,
    )

    _assert_allowed(result)


def test_push_followed_by_glued_semicolon_is_allowed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _set_upstream(repo)

    for command in ("git push origin main; echo $?", "git push origin main;echo $?"):
        result = run_hook("guard_git.py", _bash(command, repo), project_dir=repo)
        _assert_allowed(result)


def test_dangerous_command_after_glued_semicolon_is_denied() -> None:
    for command in (
        "git status; git push -f origin main",
        "echo ready;git reset --hard HEAD~1",
    ):
        output = hook_output(run_hook("guard_git.py", _bash(command, Path.cwd())))
        assert output["permissionDecision"] == "deny", command


def test_quoted_semicolon_and_dangerous_text_are_allowed() -> None:
    for command in ('echo "a; b" && git status', 'echo "git push -f"; echo done'):
        _assert_allowed(run_hook("guard_git.py", _bash(command, Path.cwd())))


def test_logical_and_pipe_operators_split_segments() -> None:
    for command in (
        "true && git push -f origin main",
        "false || git push --force origin main",
        "echo x | git reset --hard HEAD~1",
    ):
        output = hook_output(run_hook("guard_git.py", _bash(command, Path.cwd())))
        assert output["permissionDecision"] == "deny", command


def test_redirection_and_operators_in_non_git_command_is_allowed() -> None:
    for command in ("echo hi 2>&1 | tee x", "echo hi > /dev/null", "a || b | c"):
        _assert_allowed(run_hook("guard_git.py", _bash(command, Path.cwd())))
