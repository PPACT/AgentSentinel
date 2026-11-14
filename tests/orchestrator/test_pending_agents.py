"""pending/*.lock 惰性发现：锁被持有=进程存活；能拿到锁=残留并清理。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psutil  # type: ignore[import-untyped]  # psutil 未随包提供 stubs
from tools.orchestrator.locking import FileLock, lock_file_name
from tools.orchestrator.service import Daemon

ROOT = Path(__file__).resolve().parents[2]


def _create_time() -> float:
    return float(psutil.Process(os.getpid()).create_time())


def _hold(
    home: Path,
    *,
    role: str = "dev",
    created: float | None = None,
) -> tuple[FileLock, Path]:
    pending = home / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    path = pending / lock_file_name(
        role, os.getpid(), _create_time() if created is None else created
    )
    lock = FileLock(path)
    assert lock.acquire()
    return lock, path


def _touch(home: Path, name: str) -> Path:
    pending = home / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    path = pending / name
    path.write_bytes(b"")
    return path


def test_live_lock_is_registered(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "orch")
    lock, path = _hold(daemon.home)
    try:
        changed, registered, pruned = daemon.reconcile_pending_agents()

        assert (changed, registered, pruned) == (True, 1, 0)
        record = daemon.registry.get("dev")
        assert record is not None
        assert record.pid == os.getpid()
        assert record.project == daemon.project
        assert record.command == f"lockfile {path.name}"
        assert path.exists()

        assert daemon.reconcile_pending_agents() == (False, 0, 0)
        assert path.exists()
    finally:
        lock.release()


def test_released_lock_is_removed(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "orch")
    lock, path = _hold(daemon.home)
    lock.release()

    changed, registered, pruned = daemon.reconcile_pending_agents()

    assert (changed, registered, pruned) == (False, 0, 1)
    assert not path.exists()
    assert daemon.registry.get("dev") is None


def test_invalid_lock_names_are_removed(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "orch")
    bad = _touch(daemon.home, "bad.lock")
    bad_role = _touch(daemon.home, "Not-A-Role_123_456.lock")

    changed, registered, pruned = daemon.reconcile_pending_agents()

    assert (changed, registered, pruned) == (False, 0, 2)
    assert not bad.exists()
    assert not bad_role.exists()


def test_legacy_json_pending_is_cleaned(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "orch")
    legacy = _touch(daemon.home, "old-session.json")
    legacy.write_text(json.dumps({"role": "dev"}), encoding="utf-8")

    changed, registered, pruned = daemon.reconcile_pending_agents()

    assert (changed, registered, pruned) == (False, 0, 1)
    assert not legacy.exists()


def test_duplicate_live_role_keeps_newest(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "orch")
    older, older_path = _hold(daemon.home, created=1_600_000_000.0)
    newer, newer_path = _hold(daemon.home, created=1_600_000_100.0)
    try:
        changed, registered, pruned = daemon.reconcile_pending_agents()

        assert (changed, registered, pruned) == (True, 1, 0)
        record = daemon.registry.get("dev")
        assert record is not None
        assert record.command == f"lockfile {newer_path.name}"
        assert older_path.exists()
        assert newer_path.exists()
    finally:
        older.release()
        newer.release()


def test_startup_reconciles_lock(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    home.mkdir(parents=True, exist_ok=True)
    lock, path = _hold(home)
    try:
        daemon = Daemon(home)
        record = daemon.registry.get("dev")
        assert record is not None
        assert record.pid == os.getpid()
        assert path.exists()
    finally:
        lock.release()


def test_discover_reports_pending_counts(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "orch")
    lock, _path = _hold(daemon.home)
    try:
        result = daemon.discover()

        assert result["pending_registered"] == 1
        assert result["pending_pruned"] == 0
        assert [agent["role"] for agent in result["agents"]] == ["dev"]
    finally:
        lock.release()
