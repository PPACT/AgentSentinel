"""H4 PII / 密钥夹具守卫的正反样本。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from tests.hooks.hook_utils import hook_output, run_hook

_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_CODES = "10X98765432"


def _write(content: str, project_dir: Path) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(project_dir / "fixture.py"), "content": content},
        "cwd": str(project_dir),
    }


def _run(content: str, project_dir: Path) -> subprocess.CompletedProcess[str]:
    return run_hook("guard_fixtures.py", _write(content, project_dir), project_dir=project_dir)


def _assert_allowed(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def _id_with_checksum(region: str, birth: str, sequence: str) -> str:
    body = region + birth + sequence
    total = sum(int(digit) * weight for digit, weight in zip(body, _ID_WEIGHTS, strict=True))
    return body + _ID_CHECK_CODES[total % 11]


def test_realistic_id_card_is_denied(tmp_path: Path) -> None:
    # 659999 不是已分配的区划码（无效行政区划），仅用于验证“真实形态”分支。
    sample = _id_with_checksum("659999", "19900101", "123")

    output = hook_output(_run(f"id_card = {sample}\n", tmp_path))

    assert output["permissionDecision"] == "deny"


def test_synthetic_id_card_is_allowed(tmp_path: Path) -> None:
    _assert_allowed(_run("id_card = 999999199001011234\n", tmp_path))


def test_stripe_live_fixture_is_denied(tmp_path: Path) -> None:
    sample = "sk_" + "live_" + "a1" * 12

    output = hook_output(_run(f'key = "{sample}"\n', tmp_path))

    assert output["permissionDecision"] == "deny"


def test_placeholder_is_allowed(tmp_path: Path) -> None:
    placeholder = "sk-" + "x" * 17

    _assert_allowed(_run(f'key = "{placeholder}"\n', tmp_path))


def test_sk_test_placeholder_is_allowed(tmp_path: Path) -> None:
    sample = "sk_" + "test_" + "abc123"

    _assert_allowed(_run(f'key = "{sample}"\n', tmp_path))


def test_fixture_marker_is_allowed(tmp_path: Path) -> None:
    content = "pass" + 'word = "hunter2"  # secret-fixture\n'

    _assert_allowed(_run(content, tmp_path))


def test_private_ip_is_denied(tmp_path: Path) -> None:
    private_ip = "10." + "1.2.3"

    output = hook_output(_run(f"host = {private_ip}\n", tmp_path))

    assert output["permissionDecision"] == "deny"


def test_edit_new_string_is_scanned(tmp_path: Path) -> None:
    sample = "sk_" + "live_" + "a1" * 12
    payload: dict[str, Any] = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(tmp_path / "fixture.py"),
            "old_string": "pass",
            "new_string": f'key = "{sample}"',
        },
        "cwd": str(tmp_path),
    }

    output = hook_output(run_hook("guard_fixtures.py", payload, project_dir=tmp_path))

    assert output["permissionDecision"] == "deny"


def test_clean_content_is_allowed(tmp_path: Path) -> None:
    _assert_allowed(_run("value = 42\n", tmp_path))


def test_security_test_file_is_exempt(tmp_path: Path) -> None:
    sample = "sk_" + "live_" + "a1" * 12
    payload: dict[str, Any] = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(tmp_path / "tests" / "hooks" / "test_guard_demo.py"),
            "content": f'key = "{sample}"\n',
        },
        "cwd": str(tmp_path),
    }

    _assert_allowed(run_hook("guard_fixtures.py", payload, project_dir=tmp_path))


def test_file_header_marker_exempts_whole_content(tmp_path: Path) -> None:
    sample = "sk_" + "live_" + "a1" * 12
    content = "# secret-fixture\n" + f'key = "{sample}"\n'

    _assert_allowed(_run(content, tmp_path))
