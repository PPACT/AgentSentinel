"""H3 文件权责矩阵与 README 写保护的正反样本。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from tests.hooks.hook_utils import hook_output, run_hook


def _write(file_path: Path, project_dir: Path) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(file_path), "content": "x\n"},
        "cwd": str(project_dir),
    }


def _run(file_path: Path, project_dir: Path, role: str | None) -> subprocess.CompletedProcess[str]:
    env = {"SENTINEL_ROLE": role} if role is not None else None
    return run_hook(
        "guard_paths.py",
        _write(file_path, project_dir),
        project_dir=project_dir,
        env=env,
    )


def _assert_allowed(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_dev_cannot_write_readme(tmp_path: Path) -> None:
    output = hook_output(_run(tmp_path / "README.md", tmp_path, "dev"))

    assert output["permissionDecision"] == "deny"


def test_doc_readme_write_asks(tmp_path: Path) -> None:
    output = hook_output(_run(tmp_path / "README.md", tmp_path, "doc"))

    assert output["permissionDecision"] == "ask"


def test_missing_role_degrades_to_ask(tmp_path: Path) -> None:
    output = hook_output(_run(tmp_path / "README.md", tmp_path, None))

    assert output["permissionDecision"] == "ask"


def test_dev_can_write_code(tmp_path: Path) -> None:
    _assert_allowed(_run(tmp_path / "agentsentinel" / "main.py", tmp_path, "dev"))


def test_doc_cannot_write_code(tmp_path: Path) -> None:
    output = hook_output(_run(tmp_path / "tests" / "test_x.py", tmp_path, "doc"))

    assert output["permissionDecision"] == "deny"


def test_dev_cannot_write_docs(tmp_path: Path) -> None:
    output = hook_output(_run(tmp_path / "docs" / "guide.md", tmp_path, "dev"))

    assert output["permissionDecision"] == "deny"


def test_doc_can_write_docs(tmp_path: Path) -> None:
    _assert_allowed(_run(tmp_path / "docs" / "guide.md", tmp_path, "doc"))


def test_exchange_files_are_shared(tmp_path: Path) -> None:
    exchange = tmp_path / "docs" / "local" / "交流区" / "STATUS.md"

    _assert_allowed(_run(exchange, tmp_path, "dev"))
    _assert_allowed(_run(exchange, tmp_path, "doc"))


def test_env_files_are_denied_for_both_roles(tmp_path: Path) -> None:
    for role in ("dev", "doc"):
        output = hook_output(_run(tmp_path / ".env", tmp_path, role))
        assert output["permissionDecision"] == "deny"


def test_env_example_is_allowed_for_dev(tmp_path: Path) -> None:
    _assert_allowed(_run(tmp_path / ".env.example", tmp_path, "dev"))


def test_requirements_dev_asks_doc_denied(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"

    assert hook_output(_run(requirements, tmp_path, "dev"))["permissionDecision"] == "ask"
    assert hook_output(_run(requirements, tmp_path, "doc"))["permissionDecision"] == "deny"


def test_role_prompt_matrix(tmp_path: Path) -> None:
    prompt = tmp_path / ".claude" / "agents" / "dev-agent.md"

    assert hook_output(_run(prompt, tmp_path, "dev"))["permissionDecision"] == "deny"
    assert hook_output(_run(prompt, tmp_path, "doc"))["permissionDecision"] == "ask"


def test_outside_project_is_allowed(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"

    _assert_allowed(_run(outside, tmp_path, None))


def test_neutral_project_path_is_allowed_without_role(tmp_path: Path) -> None:
    _assert_allowed(_run(tmp_path / "workspace" / "out.txt", tmp_path, None))


def test_security_test_file_allowed_without_role(tmp_path: Path) -> None:
    _assert_allowed(_run(tmp_path / "tests" / "hooks" / "test_guard_demo.py", tmp_path, None))
