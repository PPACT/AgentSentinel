"""PID File 惰性发现：pending/<session>.json 的纳入与清理。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psutil  # type: ignore[import-untyped]  # psutil 未随包提供 stubs
from tools.orchestrator.service import PENDING_CLEANUP_THRESHOLD, Daemon

ROOT = Path(__file__).resolve().parents[2]


def _create_time() -> float:
    return float(psutil.Process(os.getpid()).create_time())


def _write_pending(
    home: Path,
    name: str,
    *,
    role: str = "dev",
    pid: int | None = None,
    create_time: float | None = None,
    cwd: str | None = None,
) -> Path:
    pending = home / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    path = pending / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid() if pid is None else pid,
                "pid_create_time": _create_time() if create_time is None else create_time,
                "role": role,
                "cwd": str(ROOT) if cwd is None else cwd,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_live_process_is_registered(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "orch")
    path = _write_pending(daemon.home, "s1")

    changed, registered, pruned = daemon.reconcile_pending_agents()

    assert (changed, registered, pruned) == (True, 1, 0)
    record = daemon.registry.get("dev")
    assert record is not None
    assert record.pid == os.getpid()
    assert record.project == daemon.project
    assert record.command.startswith("pidfile ")
    assert path.exists()

    assert daemon.reconcile_pending_agents() == (False, 0, 0)
    assert path.exists()


def test_dead_pid_file_is_removed(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "orch")
    path = _write_pending(daemon.home, "dead", pid=999_999_999, create_time=1.0)

    changed, registered, pruned = daemon.reconcile_pending_agents()

    assert (changed, registered, pruned) == (False, 0, 1)
    assert not path.exists()
    assert daemon.registry.get("dev") is None


def test_create_time_mismatch_is_removed(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "orch")
    path = _write_pending(daemon.home, "reused", create_time=1.0)

    changed, registered, pruned = daemon.reconcile_pending_agents()

    assert (changed, registered, pruned) == (False, 0, 1)
    assert not path.exists()
    assert daemon.registry.get("dev") is None


def test_invalid_json_and_role_are_removed(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "orch")
    pending = daemon.home / "pending"
    pending.mkdir(parents=True)
    bad_json = pending / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    bad_role = _write_pending(daemon.home, "bad-role", role="Not A Role")

    changed, registered, pruned = daemon.reconcile_pending_agents()

    assert (changed, registered, pruned) == (False, 0, 2)
    assert not bad_json.exists()
    assert not bad_role.exists()


def test_other_project_file_is_kept(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "orch")
    other = tmp_path / "other-project"
    path = _write_pending(daemon.home, "other", cwd=str(other))

    changed, registered, pruned = daemon.reconcile_pending_agents()

    assert (changed, registered, pruned) == (False, 0, 0)
    assert path.exists()
    assert daemon.registry.get("dev") is None


def test_startup_reconciles_pending(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    _write_pending(home, "startup")

    daemon = Daemon(home)

    record = daemon.registry.get("dev")
    assert record is not None
    assert record.pid == os.getpid()


def test_over_threshold_forces_dedupe(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "orch")
    for index in range(PENDING_CLEANUP_THRESHOLD + 1):
        _write_pending(daemon.home, f"dup-{index:03d}")

    changed, registered, pruned = daemon.reconcile_pending_agents()

    assert changed is True
    assert registered == 1
    assert pruned == PENDING_CLEANUP_THRESHOLD
    assert len(list((daemon.home / "pending").glob("*.json"))) == 1


def test_discover_reports_pending_counts(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "orch")
    _write_pending(daemon.home, "discover")

    result = daemon.discover()

    assert result["pending_registered"] == 1
    assert result["pending_pruned"] == 0
    assert [agent["role"] for agent in result["agents"]] == ["dev"]
