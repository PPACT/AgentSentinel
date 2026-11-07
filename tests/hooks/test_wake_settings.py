"""极简唤醒 v3：settings 只保留 PreToolUse + SessionStart 兜底。"""

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
    REPO_ROOT / "scripts" / "dev_start.ps1",
    REPO_ROOT / "scripts" / "dev_start.cmd",
    REPO_ROOT / ".claude" / "hooks" / "wake_on_exchange.py",
    REPO_ROOT / "tests" / "hooks" / "test_wake_on_exchange.py",
    REPO_ROOT / ".claude" / "hooks" / "handoff_watch.py",
    REPO_ROOT / ".claude" / "hooks" / "handoff_notify.py",
    REPO_ROOT / ".claude" / "hooks" / "session_context.py",
)


def _settings_text() -> str:
    return SETTINGS_PATH.read_text(encoding="utf-8")


def _hooks() -> dict[str, Any]:
    data = json.loads(_settings_text())
    assert isinstance(data, dict)
    hooks = data.get("hooks")
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


def _matchers(entries: object) -> list[str]:
    assert isinstance(entries, list)
    matchers: list[str] = []
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("matcher"), str):
            matchers.append(entry["matcher"])
    return matchers


def test_hooks_are_only_pre_tool_use_and_session_start() -> None:
    hooks = _hooks()

    assert set(hooks) == {"PreToolUse", "SessionStart"}
    assert "PostToolUse" not in hooks
    assert "Stop" not in hooks


def test_session_start_runs_session_bootstrap() -> None:
    entries = _hooks().get("SessionStart")
    commands = _commands(entries)
    assert len(commands) == 1
    assert commands[0].endswith('.claude/hooks/session_bootstrap.py"')


def test_pre_tool_use_guards_stay_intact() -> None:
    entries = _hooks().get("PreToolUse")
    commands = _commands(entries)

    assert any(command.endswith('guard_git.py"') for command in commands)
    assert any(command.endswith('guard_paths.py"') for command in commands)
    assert any(command.endswith('guard_fixtures.py"') for command in commands)


def test_task_and_agent_are_denied_by_matcher() -> None:
    entries = _hooks().get("PreToolUse")
    assert isinstance(entries, list)
    subagent_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict) and "guard_subagents.py" in " ".join(_commands([entry]))
    ]

    assert len(subagent_entries) == 1
    matcher = subagent_entries[0].get("matcher")
    assert isinstance(matcher, str)
    assert set(matcher.split("|")) == {"Task", "Agent"}


def test_removed_hooks_and_tests_do_not_exist() -> None:
    for path in _REMOVED_FILES:
        assert not path.exists(), f"{path} should be deleted"


def test_settings_reference_no_removed_scripts() -> None:
    text = _settings_text()

    assert "wake_on_exchange" not in text
    assert "dev_start" not in text
    assert "PostToolUse" not in text
    assert '"Stop"' not in text


def test_wake_cli_only_exposes_register_and_resolve() -> None:
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
    for command in ("register", "resolve"):
        assert command in result.stdout
    for removed in ("notify", "spawn", "heartbeat", "whoami", "prune"):
        assert removed not in result.stdout
