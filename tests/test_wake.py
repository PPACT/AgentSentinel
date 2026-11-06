"""scripts/wake.py：去重、防抖、tail 上限、mark 与 prune。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
WAKE_SCRIPT = REPO_ROOT / "scripts" / "wake.py"


def _run(project_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(WAKE_SCRIPT), *args],
        cwd=str(project_dir),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )


def _inbox(project_dir: Path) -> Path:
    return project_dir / ".claude" / "mailbox" / "inbox.jsonl"


def _entries(project_dir: Path) -> list[dict[str, Any]]:
    path = _inbox(project_dir)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_entries(project_dir: Path, entries: list[dict[str, Any]]) -> None:
    path = _inbox(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
        encoding="utf-8",
    )


def _post(project_dir: Path, **overrides: str) -> dict[str, Any]:
    args = {
        "from": "doc",
        "to": "dev",
        "topic": "P2-3c",
        "event": "delivered",
        "commit": "b3c6857",
    }
    args.update(overrides)
    command = ["post"]
    for key, value in args.items():
        command.extend([f"--{key}", value])
    result = _run(project_dir, *command)
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    return dict(parsed)


def test_post_writes_pointer_and_degrades_without_slots(tmp_path: Path) -> None:
    out = _post(tmp_path)

    assert out["posted"] is True
    assert out["peer"] is None
    entry = out["entry"]
    assert isinstance(entry, dict)
    assert entry["from"] == "doc"
    assert entry["to"] == "dev"
    assert entry["topic"] == "P2-3c"
    assert entry["event"] == "delivered"
    assert entry["commit"] == "b3c6857"
    assert entry["status"] == "unread"
    assert entry["notified"] is False
    assert entry["actionable"] is True
    assert len(entry["id"]) == 8
    assert len(_entries(tmp_path)) == 1


def test_post_dedupes_unread_key(tmp_path: Path) -> None:
    first = _post(tmp_path)
    entry = first["entry"]
    assert isinstance(entry, dict)
    marked = _run(tmp_path, "mark-notified", "--id", str(entry["id"]))
    assert marked.returncode == 0, marked.stderr

    second = _post(tmp_path)

    assert second["deduped"] is True
    assert second["posted"] is False
    assert len(_entries(tmp_path)) == 1


def test_actionable_posts_do_not_merge_within_debounce_window(tmp_path: Path) -> None:
    _post(tmp_path, topic="first")
    second = _post(tmp_path, topic="second")

    assert second["merged"] is False
    entries = _entries(tmp_path)
    assert len(entries) == 2
    assert entries[0]["topic"] == "first"
    assert entries[1]["topic"] == "second"


def _passive_post(tmp_path: Path, topic: str) -> dict[str, Any]:
    result = _run(
        tmp_path,
        "post",
        "--from",
        "doc",
        "--to",
        "dev",
        "--topic",
        topic,
        "--event",
        "changed:STATUS.md",
        "--passive",
    )
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    return dict(parsed)


def test_passive_post_is_not_actionable(tmp_path: Path) -> None:
    out = _passive_post(tmp_path, "changed")

    entry = out["entry"]
    assert isinstance(entry, dict)
    assert entry["actionable"] is False


def test_actionable_entry_is_not_overwritten_by_passive_merge(tmp_path: Path) -> None:
    _post(tmp_path, topic="delivered")
    _passive_post(tmp_path, "changed")

    entries = _entries(tmp_path)
    assert len(entries) == 2
    assert entries[0]["topic"] == "delivered"
    assert entries[0]["actionable"] is True
    assert entries[1]["topic"] == "changed"
    assert entries[1]["actionable"] is False


def test_passive_posts_merge_within_debounce_window(tmp_path: Path) -> None:
    _passive_post(tmp_path, "first-change")
    second = _passive_post(tmp_path, "second-change")

    assert second["merged"] is True
    entries = _entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["topic"] == "second-change"
    assert entries[0]["actionable"] is False


def test_tail_limits_recent_entries(tmp_path: Path) -> None:
    _write_entries(
        tmp_path,
        [
            {"id": f"id-{index:02d}", "from": "doc", "to": "dev", "status": "unread"}
            for index in range(12)
        ],
    )

    result = _run(tmp_path, "tail", "--limit", "10")

    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line]
    assert len(lines) == 10
    assert json.loads(lines[-1])["id"] == "id-11"


def test_mark_read_and_notified(tmp_path: Path) -> None:
    entry = _post(tmp_path)["entry"]
    assert isinstance(entry, dict)
    entry_id = str(entry["id"])

    read = _run(tmp_path, "mark-read", "--id", entry_id)
    notified = _run(tmp_path, "mark-notified", "--id", entry_id)

    assert read.returncode == 0, read.stderr
    assert notified.returncode == 0, notified.stderr
    stored = _entries(tmp_path)[0]
    assert stored["status"] == "read"
    assert stored["notified"] is True
    assert isinstance(stored["read_at"], str)


def test_prune_moves_read_and_notified_entries(tmp_path: Path) -> None:
    _write_entries(
        tmp_path,
        [
            {
                "id": f"read-{index}",
                "status": "read",
                "notified": True,
            }
            for index in range(3)
        ]
        + [{"id": "unread-1", "status": "unread", "notified": False}],
    )

    result = _run(tmp_path, "prune")

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["archived"] == 3
    assert summary["remaining"] == 1
    archive = tmp_path / ".claude" / "mailbox" / "archive.jsonl"
    assert len(archive.read_text(encoding="utf-8").splitlines()) == 3
    assert [entry["id"] for entry in _entries(tmp_path)] == ["unread-1"]


def test_prune_does_not_archive_unread_entries(tmp_path: Path) -> None:
    _write_entries(
        tmp_path,
        [{"id": f"id-{index}", "status": "unread", "notified": False} for index in range(55)],
    )

    result = _run(tmp_path, "prune")

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["archived"] == 0
    assert summary["remaining"] == 55
    assert "warning" in summary
    assert len(_entries(tmp_path)) == 55
    assert not (tmp_path / ".claude" / "mailbox" / "archive.jsonl").exists()


def test_slots_resolve_peer_name(tmp_path: Path) -> None:
    mailbox = tmp_path / ".claude" / "mailbox"
    mailbox.mkdir(parents=True)
    (mailbox / "slots.json").write_text(json.dumps({"dev": "agentsentinel-4a"}), encoding="utf-8")

    out = _post(tmp_path)

    assert out["peer"] == "agentsentinel-4a"


def test_invalid_slots_json_degrades_silently(tmp_path: Path) -> None:
    mailbox = tmp_path / ".claude" / "mailbox"
    mailbox.mkdir(parents=True)
    (mailbox / "slots.json").write_text("{not json", encoding="utf-8")

    out = _post(tmp_path)

    assert out["peer"] is None
    assert out["posted"] is True


def test_post_hashes_path_argument(tmp_path: Path) -> None:
    target = tmp_path / "STATUS.md"
    target.write_text("hello", encoding="utf-8")

    out = _post(tmp_path, path="STATUS.md")

    entry = out["entry"]
    assert isinstance(entry, dict)
    assert entry["file_hash"] == hashlib.sha256(b"hello").hexdigest()
