"""handoff_notify（Stop）：未通知阻断 2 次、第 3 次放行、stop_hook_active 与静默路径。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from tests.hooks.hook_utils import run_hook


def _seed(project_dir: Path, entries: list[dict[str, Any]]) -> None:
    mailbox = project_dir / ".claude" / "mailbox"
    mailbox.mkdir(parents=True, exist_ok=True)
    (mailbox / "inbox.jsonl").write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
        encoding="utf-8",
    )


def _entry(entry_id: str = "abc12345", **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": entry_id,
        "from": "dev",
        "to": "doc",
        "topic": "P2-3c",
        "event": "delivered",
        "actionable": True,
        "status": "unread",
        "notified": False,
        "notify_attempts": 0,
    }
    entry.update(overrides)
    return entry


def _run(
    project_dir: Path,
    *,
    stop_hook_active: bool = False,
    role: str = "dev",
) -> subprocess.CompletedProcess[str]:
    payload = {
        "hook_event_name": "Stop",
        "stop_hook_active": stop_hook_active,
        "cwd": str(project_dir),
    }
    return run_hook(
        "handoff_notify.py",
        payload,
        project_dir=project_dir,
        env={"SENTINEL_ROLE": role},
    )


def _entries(project_dir: Path) -> list[dict[str, Any]]:
    path = project_dir / ".claude" / "mailbox" / "inbox.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == 0, result.stderr
    text = result.stdout.strip()
    assert text, "expected Stop hook JSON output"
    parsed = json.loads(text)
    assert isinstance(parsed, dict)
    return dict(parsed)


def test_blocks_twice_then_allows_with_system_message(tmp_path: Path) -> None:
    _seed(tmp_path, [_entry()])

    first = _output(_run(tmp_path))
    assert first["decision"] == "block"
    assert "SendMessage(to=doc)" in first["reason"]
    assert _entries(tmp_path)[0]["notify_attempts"] == 1

    second = _output(_run(tmp_path))
    assert second["decision"] == "block"
    assert _entries(tmp_path)[0]["notify_attempts"] == 2

    third = _output(_run(tmp_path))
    assert "decision" not in third
    assert "systemMessage" in third
    assert _entries(tmp_path)[0]["notify_attempts"] == 3

    fourth = _run(tmp_path)
    assert fourth.returncode == 0, fourth.stderr
    assert fourth.stdout.strip() == ""


def test_stop_hook_active_does_not_block(tmp_path: Path) -> None:
    _seed(tmp_path, [_entry()])

    result = _run(tmp_path, stop_hook_active=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    assert _entries(tmp_path)[0]["notify_attempts"] == 0


def test_no_unnotified_entries_is_silent(tmp_path: Path) -> None:
    _seed(tmp_path, [_entry(notified=True)])

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_missing_role_is_silent(tmp_path: Path) -> None:
    _seed(tmp_path, [_entry()])

    result = _run(tmp_path, role="")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_passive_entries_do_not_trigger(tmp_path: Path) -> None:
    _seed(tmp_path, [_entry(actionable=False, event="changed:STATUS.md")])

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    assert _entries(tmp_path)[0]["notify_attempts"] == 0


def test_entries_from_other_role_do_not_trigger(tmp_path: Path) -> None:
    _seed(tmp_path, [_entry(**{"from": "doc"})])

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
