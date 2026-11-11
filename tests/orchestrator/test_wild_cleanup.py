"""未注册 node/python 残留进程的强制清理端点。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import psutil  # type: ignore[import-untyped]  # psutil 未随包提供 stubs
from tools.orchestrator import service
from tools.orchestrator.service import Daemon

ROOT = Path(__file__).resolve().parents[2]


class _FakeProcess:
    def __init__(self, pid: int, name: str, create_time: float, cwd: str) -> None:
        self.pid = pid
        self._name = name
        self._create_time = create_time
        self._cwd = cwd

    def name(self) -> str:
        return self._name

    def create_time(self) -> float:
        return self._create_time

    def cwd(self) -> str:
        return self._cwd

    def ppid(self) -> int:
        return 1


def _factory(process: _FakeProcess) -> Any:
    def create(pid: int) -> _FakeProcess:
        if pid == process.pid:
            return process
        raise psutil.NoSuchProcess(pid)

    return create


def _daemon(tmp_path: Path) -> Daemon:
    return Daemon(tmp_path / "orch")


def test_cleanup_wild_kills_allowed_node_process(tmp_path: Path, monkeypatch: Any) -> None:
    daemon = _daemon(tmp_path)
    fake = _FakeProcess(4321, "node.exe", 100.0, str(ROOT))
    calls: list[tuple[int, float | None]] = []

    def fake_kill(pid: int, create_time: float | None = None, **_kwargs: Any) -> list[int]:
        calls.append((pid, create_time))
        return [pid]

    monkeypatch.setattr(service, "kill_process_tree", fake_kill)

    status, body = daemon.cleanup_wild(
        {"pid": 4321, "create_time": 100.0}, process_factory=_factory(fake)
    )

    assert status == 200
    assert body == {"cleaned": True, "pid": 4321, "killed": [4321]}
    assert calls == [(4321, 100.0)]


def test_cleanup_wild_rejects_non_allowlisted_process(tmp_path: Path, monkeypatch: Any) -> None:
    daemon = _daemon(tmp_path)
    fake = _FakeProcess(4321, "claude.exe", 100.0, str(ROOT))
    monkeypatch.setattr(
        service, "kill_process_tree", lambda *_args, **_kwargs: pytest_fail_if_called()
    )

    status, body = daemon.cleanup_wild(
        {"pid": 4321, "create_time": 100.0}, process_factory=_factory(fake)
    )

    assert status == 400
    assert body["reason"] == "not_cleanable"


def test_cleanup_wild_rejects_reused_pid(tmp_path: Path, monkeypatch: Any) -> None:
    daemon = _daemon(tmp_path)
    fake = _FakeProcess(4321, "python.exe", 200.0, str(ROOT))
    monkeypatch.setattr(
        service, "kill_process_tree", lambda *_args, **_kwargs: pytest_fail_if_called()
    )

    status, body = daemon.cleanup_wild(
        {"pid": 4321, "create_time": 100.0}, process_factory=_factory(fake)
    )

    assert status == 409
    assert body["reason"] == "stale_process"


def test_cleanup_wild_rejects_process_outside_project(tmp_path: Path, monkeypatch: Any) -> None:
    daemon = _daemon(tmp_path)
    fake = _FakeProcess(4321, "python.exe", 100.0, str(tmp_path))
    monkeypatch.setattr(
        service, "kill_process_tree", lambda *_args, **_kwargs: pytest_fail_if_called()
    )

    status, body = daemon.cleanup_wild(
        {"pid": 4321, "create_time": 100.0}, process_factory=_factory(fake)
    )

    assert status == 403
    assert body["reason"] == "outside_project"


def test_cleanup_wild_requires_create_time(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)

    status, body = daemon.cleanup_wild({"pid": 4321})

    assert status == 400
    assert body["reason"] == "missing_create_time"


def test_cleanup_wild_protects_daemon_and_agents(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)

    status, body = daemon.cleanup_wild({"pid": os.getpid(), "create_time": 1.0})

    assert status == 409
    assert body["reason"] == "protected_process"


def pytest_fail_if_called() -> list[int]:
    raise AssertionError("kill_process_tree must not be called")
