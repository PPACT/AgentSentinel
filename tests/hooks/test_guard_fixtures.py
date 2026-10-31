"""H4 目录级 PII/密钥扫描的正反样本。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from tests.hooks.hook_utils import hook_output, run_hook


def _write(file_path: Path, content: str, project_dir: Path) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(file_path), "content": content},
        "cwd": str(project_dir),
    }


def _run(file_path: Path, content: str, project_dir: Path) -> subprocess.CompletedProcess[str]:
    payload = _write(file_path, content, project_dir)
    return run_hook("guard_fixtures.py", payload, project_dir=project_dir)


def _assert_allowed(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def _stripe_sample() -> str:
    return "sk_" + "live_" + "a1" * 12


def test_tests_directory_is_never_scanned(tmp_path: Path) -> None:
    content = f'key = "{_stripe_sample()}"\n'

    _assert_allowed(_run(tmp_path / "tests" / "test_x.py", content, tmp_path))


def test_agentsentinel_secret_is_denied(tmp_path: Path) -> None:
    content = f'key = "{_stripe_sample()}"\n'

    output = hook_output(_run(tmp_path / "agentsentinel" / "config.py", content, tmp_path))

    assert output["permissionDecision"] == "deny"


def test_src_secret_is_denied(tmp_path: Path) -> None:
    content = f'key = "{_stripe_sample()}"\n'

    output = hook_output(_run(tmp_path / "src" / "main.py", content, tmp_path))

    assert output["permissionDecision"] == "deny"


def test_business_synthetic_id_is_allowed(tmp_path: Path) -> None:
    _assert_allowed(
        _run(tmp_path / "agentsentinel" / "x.py", "id = 999999199001011234\n", tmp_path)
    )


def test_business_placeholder_is_allowed(tmp_path: Path) -> None:
    placeholder = "sk-" + "x" * 17

    _assert_allowed(_run(tmp_path / "src" / "x.py", f'key = "{placeholder}"\n', tmp_path))


def test_docs_directory_is_not_scanned(tmp_path: Path) -> None:
    content = f'key = "{_stripe_sample()}"\n'

    _assert_allowed(_run(tmp_path / "docs" / "note.md", content, tmp_path))


def test_edit_new_string_in_business_is_denied(tmp_path: Path) -> None:
    payload: dict[str, Any] = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(tmp_path / "agentsentinel" / "x.py"),
            "old_string": "pass",
            "new_string": f'key = "{_stripe_sample()}"',
        },
        "cwd": str(tmp_path),
    }

    output = hook_output(run_hook("guard_fixtures.py", payload, project_dir=tmp_path))

    assert output["permissionDecision"] == "deny"


def test_clean_business_write_is_allowed(tmp_path: Path) -> None:
    _assert_allowed(_run(tmp_path / "agentsentinel" / "x.py", "value = 42\n", tmp_path))
