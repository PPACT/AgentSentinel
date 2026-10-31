"""H2 依赖与环境守卫的正反样本。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from tests.hooks.hook_utils import hook_output, run_hook


def _bash(command: str, cwd: Path) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd),
    }


def _edit(file_path: Path, old: str, new: str, cwd: Path) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(file_path), "old_string": old, "new_string": new},
        "cwd": str(cwd),
    }


def _assert_allowed(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_pip_install_is_denied(tmp_path: Path) -> None:
    output = hook_output(
        run_hook("guard_deps.py", _bash("pip install requests", tmp_path), project_dir=tmp_path)
    )

    assert output["permissionDecision"] == "deny"


def test_conda_install_is_denied(tmp_path: Path) -> None:
    output = hook_output(
        run_hook("guard_deps.py", _bash("conda install numpy", tmp_path), project_dir=tmp_path)
    )

    assert output["permissionDecision"] == "deny"


def test_sudo_is_denied(tmp_path: Path) -> None:
    output = hook_output(
        run_hook("guard_deps.py", _bash("sudo apt install curl", tmp_path), project_dir=tmp_path)
    )

    assert output["permissionDecision"] == "deny"


def test_uv_pip_install_at_project_root_asks(tmp_path: Path) -> None:
    output = hook_output(
        run_hook(
            "guard_deps.py",
            _bash("uv pip install email-validator", tmp_path),
            project_dir=tmp_path,
        )
    )

    assert output["permissionDecision"] == "ask"


def test_uv_pip_install_outside_project_is_denied(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    output = hook_output(
        run_hook(
            "guard_deps.py",
            _bash("uv pip install email-validator", outside),
            project_dir=tmp_path,
        )
    )

    assert output["permissionDecision"] == "deny"


def test_requirements_edit_asks(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    old = "detect-secrets==1.5.0\nGitPython>=3.1.42"
    new = "detect-secrets==1.5.0\nemail-validator>=2.3.0\nGitPython>=3.1.42"

    output = hook_output(
        run_hook(
            "guard_deps.py",
            _edit(requirements, old, new, tmp_path),
            project_dir=tmp_path,
        )
    )

    assert output["permissionDecision"] == "ask"


def test_core_version_change_is_denied(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"

    output = hook_output(
        run_hook(
            "guard_deps.py",
            _edit(requirements, "agentscope[full]==2.0.8", "agentscope[full]==2.1.0", tmp_path),
            project_dir=tmp_path,
        )
    )

    assert output["permissionDecision"] == "deny"


def test_requirements_write_with_valid_pins_asks(tmp_path: Path) -> None:
    payload: dict[str, Any] = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(tmp_path / "requirements.txt"),
            "content": "agentscope[full]==2.0.8\ndetect-secrets==1.5.0\nemail-validator>=2.3.0\n",
        },
        "cwd": str(tmp_path),
    }

    output = hook_output(run_hook("guard_deps.py", payload, project_dir=tmp_path))

    assert output["permissionDecision"] == "ask"


def test_unrelated_command_is_allowed(tmp_path: Path) -> None:
    result = run_hook("guard_deps.py", _bash("ls -la", tmp_path), project_dir=tmp_path)

    _assert_allowed(result)
