"""scripts/wake.py：去重、防抖、tail 上限、mark 与 prune。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
WAKE_SCRIPT = REPO_ROOT / "scripts" / "wake.py"


def _run(
    project_dir: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    process_env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    process_env["PYTHONUTF8"] = "1"
    process_env.pop("SENTINEL_ROLE", None)
    if env is not None:
        process_env.update(env)
    return subprocess.run(
        [sys.executable, str(WAKE_SCRIPT), *args],
        cwd=str(project_dir),
        env=process_env,
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


def _post(
    project_dir: Path,
    *,
    env: dict[str, str] | None = None,
    **overrides: str,
) -> dict[str, Any]:
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
    result = _run(project_dir, *command, env=env)
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


def _role_path(project_dir: Path, role: str) -> Path:
    return project_dir / ".claude" / "mailbox" / "roles" / f"{role}.json"


def _register(
    project_dir: Path,
    *,
    role: str = "dev",
    session: str = "session-dev",
    ref: str = "abcd12",
    force: bool = False,
) -> subprocess.CompletedProcess[str]:
    args = ["register", "--role", role, "--session", session, "--ref", ref]
    if force:
        args.append("--force")
    return _run(project_dir, *args)


def test_register_writes_registration_and_lock(tmp_path: Path) -> None:
    result = _register(tmp_path)

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["registered"] is True
    info = out["registration"]
    assert info["role"] == "dev"
    assert info["session"] == "session-dev"
    assert info["ref"] == "abcd12"
    assert isinstance(info["pid"], int)
    assert info["started_at"]
    assert info["heartbeat_at"]
    assert _role_path(tmp_path, "dev").is_file()
    assert (tmp_path / ".claude" / "mailbox" / "roles" / "dev.lock").is_file()
    stored = json.loads(_role_path(tmp_path, "dev").read_text(encoding="utf-8"))
    assert stored["session"] == "session-dev"


def test_duplicate_active_registration_conflicts(tmp_path: Path) -> None:
    _register(tmp_path, session="first-window")

    result = _register(tmp_path, session="second-window")

    assert result.returncode == 1
    out = json.loads(result.stdout)
    assert out["registered"] is False
    assert "first-window" in out["reason"]
    assert out["active"]["session"] == "first-window"


def test_force_takes_over_active_registration(tmp_path: Path) -> None:
    _register(tmp_path, session="first-window")

    result = _register(tmp_path, session="second-window", force=True)

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["registered"] is True
    assert out["registration"]["session"] == "second-window"
    stored = json.loads(_role_path(tmp_path, "dev").read_text(encoding="utf-8"))
    assert stored["session"] == "second-window"


def test_stale_registration_allows_reregister(tmp_path: Path) -> None:
    _register(tmp_path, session="old-window")
    path = _role_path(tmp_path, "dev")
    data = json.loads(path.read_text(encoding="utf-8"))
    old = datetime.now(UTC) - timedelta(seconds=1200)
    data["heartbeat_at"] = old.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    path.write_text(json.dumps(data), encoding="utf-8")

    result = _register(tmp_path, session="new-window")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["registration"]["session"] == "new-window"


def test_whoami_reports_role_and_registration(tmp_path: Path) -> None:
    before = _run(tmp_path, "whoami", env={"SENTINEL_ROLE": "dev"})
    assert before.returncode == 0, before.stderr
    before_out = json.loads(before.stdout)
    assert before_out["role"] == "dev"
    assert before_out["status"] == "inactive"
    assert before_out["registration"] is None

    _register(tmp_path)

    after = json.loads(_run(tmp_path, "whoami", env={"SENTINEL_ROLE": "dev"}).stdout)
    assert after["status"] == "active"
    assert after["stale"] is False
    assert after["registration"]["session"] == "session-dev"


def test_session_role_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "session-role").write_text("dev", encoding="utf-8")

    without_env = _run(tmp_path, "whoami")
    with_env = _run(tmp_path, "whoami", env={"SENTINEL_ROLE": "doc"})

    assert without_env.returncode == 0, without_env.stderr
    assert json.loads(without_env.stdout)["role"] is None
    assert json.loads(with_env.stdout)["role"] == "doc"


def test_whoami_without_role_is_inactive(tmp_path: Path) -> None:
    result = _run(tmp_path, "whoami")

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["role"] is None
    assert out["status"] == "inactive"


def test_resolve_prefers_registration_over_slots(tmp_path: Path) -> None:
    mailbox = tmp_path / ".claude" / "mailbox"
    mailbox.mkdir(parents=True)
    (mailbox / "slots.json").write_text(json.dumps({"dev": "slot-session"}), encoding="utf-8")
    _register(tmp_path, session="registered-session")

    result = _run(tmp_path, "resolve", "--role", "dev")

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["session"] == "registered-session"
    assert out["ref"] == "abcd12"
    assert out["source"] == "roles"


def test_resolve_falls_back_to_slots(tmp_path: Path) -> None:
    mailbox = tmp_path / ".claude" / "mailbox"
    mailbox.mkdir(parents=True)
    (mailbox / "slots.json").write_text(json.dumps({"dev": "slot-session"}), encoding="utf-8")

    result = _run(tmp_path, "resolve", "--role", "dev")

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["session"] == "slot-session"
    assert out["source"] == "slots"


def test_resolve_missing_exits_one(tmp_path: Path) -> None:
    result = _run(tmp_path, "resolve", "--role", "dev")

    assert result.returncode == 1
    assert result.stdout.strip() == "{}"


def test_post_uses_registered_session(tmp_path: Path) -> None:
    _register(tmp_path, role="dev", session="registered-dev")

    out = _post(tmp_path)

    assert out["peer"] == "registered-dev"


def _make_stale(project_dir: Path, role: str = "dev") -> None:
    path = _role_path(project_dir, role)
    data = json.loads(path.read_text(encoding="utf-8"))
    old = datetime.now(UTC) - timedelta(seconds=1200)
    data["heartbeat_at"] = old.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    path.write_text(json.dumps(data), encoding="utf-8")


def _heartbeat_age_seconds(project_dir: Path, role: str = "dev") -> float:
    data = json.loads(_role_path(project_dir, role).read_text(encoding="utf-8"))
    heartbeat = datetime.strptime(data["heartbeat_at"], "%Y-%m-%dT%H:%M:%S.%f%z")
    return (datetime.now(UTC) - heartbeat).total_seconds()


def test_heartbeat_refreshes_stale_registration(tmp_path: Path) -> None:
    _register(tmp_path)
    _make_stale(tmp_path)

    result = _run(tmp_path, "heartbeat", "--role", "dev")

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["refreshed"] is True
    assert out["role"] == "dev"
    assert _heartbeat_age_seconds(tmp_path) < 60


def test_heartbeat_without_registration_is_noop(tmp_path: Path) -> None:
    result = _run(tmp_path, "heartbeat", "--role", "dev")

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["refreshed"] is False
    assert out["registration"] is None


def test_whoami_refreshes_heartbeat(tmp_path: Path) -> None:
    _register(tmp_path)
    _make_stale(tmp_path)

    result = _run(tmp_path, "whoami", env={"SENTINEL_ROLE": "dev"})

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["status"] == "active"
    assert out["stale"] is False
    assert _heartbeat_age_seconds(tmp_path) < 60


def test_post_refreshes_heartbeat(tmp_path: Path) -> None:
    _register(tmp_path, role="doc", session="doc-session")
    _make_stale(tmp_path, "doc")

    _post(tmp_path, env={"SENTINEL_ROLE": "doc"})

    assert _heartbeat_age_seconds(tmp_path, "doc") < 60


def test_stale_registration_is_resolvable(tmp_path: Path) -> None:
    _register(tmp_path, session="stale-session")
    _make_stale(tmp_path)
    mailbox = tmp_path / ".claude" / "mailbox"
    (mailbox / "slots.json").write_text(json.dumps({"dev": "slot-session"}), encoding="utf-8")

    result = _run(tmp_path, "resolve", "--role", "dev")

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["session"] == "stale-session"
    assert out["source"] == "roles-stale"
    assert out["stale"] is True
