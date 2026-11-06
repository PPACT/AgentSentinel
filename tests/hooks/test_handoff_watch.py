"""handoff_watch（PostToolUse）：目标文件记录、同内容不重复、非目标静默。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from tests.hooks.hook_utils import run_hook


def _setup(tmp_path: Path) -> Path:
    exchange = tmp_path / "docs" / "local" / "交流区"
    exchange.mkdir(parents=True)
    status = exchange / "STATUS.md"
    status.write_text("# STATUS\n\n| 项 | 值 |\n", encoding="utf-8")
    return status


def _payload(target: Path, project_dir: Path, tool: str = "Edit") -> dict[str, Any]:
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(target)},
        "cwd": str(project_dir),
    }


def _entries(project_dir: Path) -> list[dict[str, Any]]:
    path = project_dir / ".claude" / "mailbox" / "inbox.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _run(
    target: Path,
    project_dir: Path,
    *,
    role: str = "doc",
) -> subprocess.CompletedProcess[str]:
    return run_hook(
        "handoff_watch.py",
        _payload(target, project_dir),
        project_dir=project_dir,
        env={"SENTINEL_ROLE": role},
    )


def test_target_file_records_one_changed_event(tmp_path: Path) -> None:
    status = _setup(tmp_path)

    result = _run(status, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    entries = _entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["from"] == "doc"
    assert entries[0]["to"] == "dev"
    assert entries[0]["event"].startswith("changed:")
    assert entries[0]["path"] == "docs/local/交流区/STATUS.md"
    assert entries[0]["file_hash"]
    assert entries[0]["actionable"] is False


def test_same_content_is_not_recorded_twice(tmp_path: Path) -> None:
    status = _setup(tmp_path)
    _run(status, tmp_path)

    second = _run(status, tmp_path)

    assert second.returncode == 0, second.stderr
    assert len(_entries(tmp_path)) == 1


def test_non_target_file_is_silent(tmp_path: Path) -> None:
    _setup(tmp_path)
    other = tmp_path / "src" / "app.py"
    other.parent.mkdir(parents=True)
    other.write_text("print('x')\n", encoding="utf-8")

    result = _run(other, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    assert _entries(tmp_path) == []


def test_missing_role_is_silent(tmp_path: Path) -> None:
    status = _setup(tmp_path)

    result = _run(status, tmp_path, role="")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    assert _entries(tmp_path) == []


def test_exchange_file_is_recorded(tmp_path: Path) -> None:
    _setup(tmp_path)
    exchange = tmp_path / "docs" / "local" / "交流区" / "交流区.md"
    exchange.write_text("### [已派] x\n", encoding="utf-8")

    result = _run(exchange, tmp_path)

    assert result.returncode == 0, result.stderr
    entries = _entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["path"] == "docs/local/交流区/交流区.md"
