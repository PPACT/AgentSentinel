"""极简唤醒 v2：settings 只保留 PreToolUse + 新 PostToolUse，旧 Hook 已删除。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"
WAKE_SCRIPT = REPO_ROOT / "scripts" / "wake.py"

_REMOVED_FILES = (
    REPO_ROOT / ".claude" / "hooks" / "handoff_watch.py",
    REPO_ROOT / ".claude" / "hooks" / "handoff_notify.py",
    REPO_ROOT / ".claude" / "hooks" / "session_context.py",
    REPO_ROOT / "tests" / "hooks" / "test_handoff_watch.py",
    REPO_ROOT / "tests" / "hooks" / "test_handoff_notify.py",
    REPO_ROOT / "tests" / "hooks" / "test_session_context.py",
)

_REMOVED_COMMANDS = (
    "heartbeat",
    "whoami",
    "prune",
    "tail",
    "mark-notified",
    "mark-read",
    "post",
)


def _settings() -> dict[str, Any]:
    data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _hooks() -> dict[str, Any]:
    hooks = _settings().get("hooks")
    assert isinstance(hooks, dict)
    return hooks


def _commands(entries: object) -> list[str]:
    assert isinstance(entries, list)
    commands: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []):
            if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                commands.append(hook["command"])
    return commands


def test_no_session_start_or_stop_hooks() -> None:
    hooks = _hooks()

    assert "SessionStart" not in hooks
    assert "Stop" not in hooks
    assert set(hooks) == {"PreToolUse", "PostToolUse"}


def test_post_tool_use_runs_wake_on_exchange_only() -> None:
    entries = _hooks().get("PostToolUse")
    assert isinstance(entries, list)
    assert len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, dict)
    assert entry.get("matcher") == "Edit|Write|MultiEdit"
    commands = _commands([entry])
    assert len(commands) == 1
    assert commands[0].endswith('.claude/hooks/wake_on_exchange.py"')


def test_pre_tool_use_guards_stay_intact() -> None:
    commands = _commands(_hooks().get("PreToolUse"))

    assert any(command.endswith('guard_git.py"') for command in commands)
    assert any(command.endswith('guard_paths.py"') for command in commands)
    assert any(command.endswith('guard_fixtures.py"') for command in commands)


def test_removed_hooks_and_tests_do_not_exist() -> None:
    for path in _REMOVED_FILES:
        assert not path.exists(), f"{path} should be deleted"


def test_wake_cli_only_exposes_three_commands() -> None:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, str(WAKE_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    for command in ("notify", "register", "resolve"):
        assert command in result.stdout
    for command in _REMOVED_COMMANDS:
        assert command not in result.stdout
