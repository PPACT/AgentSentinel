"""H3 受保护文件与日常写入放行的正反样本。"""

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


def _run(file_path: Path, project_dir: Path) -> subprocess.CompletedProcess[str]:
    return run_hook("guard_paths.py", _write(file_path, project_dir), project_dir=project_dir)


def _assert_allowed(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_readme_is_denied(tmp_path: Path) -> None:
    output = hook_output(_run(tmp_path / "README.md", tmp_path))

    assert output["permissionDecision"] == "deny"


def test_env_is_denied(tmp_path: Path) -> None:
    output = hook_output(_run(tmp_path / ".env", tmp_path))

    assert output["permissionDecision"] == "deny"


def test_env_variants_are_denied(tmp_path: Path) -> None:
    output = hook_output(_run(tmp_path / ".env.local", tmp_path))

    assert output["permissionDecision"] == "deny"


def test_env_example_is_allowed(tmp_path: Path) -> None:
    _assert_allowed(_run(tmp_path / ".env.example", tmp_path))


def test_agents_md_is_denied(tmp_path: Path) -> None:
    output = hook_output(_run(tmp_path / "AGENTS.md", tmp_path))

    assert output["permissionDecision"] == "deny"


def test_ordinary_code_write_is_allowed(tmp_path: Path) -> None:
    _assert_allowed(_run(tmp_path / "agentsentinel" / "main.py", tmp_path))


def test_test_file_write_is_allowed(tmp_path: Path) -> None:
    _assert_allowed(_run(tmp_path / "tests" / "test_x.py", tmp_path))


def test_src_write_is_allowed(tmp_path: Path) -> None:
    _assert_allowed(_run(tmp_path / "src" / "main.py", tmp_path))


def test_docs_write_is_allowed(tmp_path: Path) -> None:
    _assert_allowed(_run(tmp_path / "docs" / "guide.md", tmp_path))


def test_config_write_is_allowed(tmp_path: Path) -> None:
    _assert_allowed(_run(tmp_path / "pyproject.toml", tmp_path))


def test_outside_project_is_allowed(tmp_path: Path) -> None:
    _assert_allowed(_run(tmp_path.parent / "outside.txt", tmp_path))
