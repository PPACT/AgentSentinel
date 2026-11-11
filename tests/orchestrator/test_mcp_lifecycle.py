"""MCP server 进程生命周期：stdin EOF 与父进程死亡都必须立即退出。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil  # type: ignore[import-untyped]  # psutil 未随包提供 stubs
from tools.orchestrator import mcp_server
from tools.orchestrator.launcher import hidden_creationflags, process_alive

ROOT = Path(__file__).resolve().parents[2]
MCP_SCRIPT = ROOT / "tools" / "orchestrator" / "mcp_server.py"


class _FakeProcess:
    def __init__(self, create_time: float = 10.0, status: str = "running") -> None:
        self._create_time = create_time
        self._status = status

    def create_time(self) -> float:
        return self._create_time

    def is_running(self) -> bool:
        return True

    def status(self) -> str:
        return self._status


def test_parent_alive_detects_reused_pid() -> None:
    assert (
        mcp_server.parent_alive(1234, 10.0, process_factory=lambda _pid: _FakeProcess(10.0)) is True
    )
    assert (
        mcp_server.parent_alive(1234, 10.0, process_factory=lambda _pid: _FakeProcess(99.0))
        is False
    )


def test_parent_alive_returns_false_for_missing_process() -> None:
    def missing(pid: int) -> Any:
        raise psutil.NoSuchProcess(pid)

    assert mcp_server.parent_alive(1234, 10.0, process_factory=missing) is False


def test_watch_parent_exits_with_os_exit_when_parent_disappears() -> None:
    exits: list[int] = []
    answers = iter([True, False])

    mcp_server.watch_parent(
        4321,
        10.0,
        alive=lambda _pid, _created: next(answers),
        sleep=lambda _seconds: None,
        exit_process=exits.append,
        max_cycles=5,
    )

    assert exits == [0]


def test_watch_parent_keeps_running_while_parent_alive() -> None:
    exits: list[int] = []

    mcp_server.watch_parent(
        4321,
        10.0,
        alive=lambda _pid, _created: True,
        sleep=lambda _seconds: None,
        exit_process=exits.append,
        max_cycles=3,
    )

    assert exits == []


def _env(home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(home)
    environment["SENTINEL_ORCH_PARENT_CHECK_SECONDS"] = "0.2"
    environment.pop("SENTINEL_ROLE", None)
    environment.pop("CLAUDE_CODE_SESSION_ID", None)
    return environment


def _wait_dead(pid: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return False


def test_mcp_server_exits_on_stdin_eof(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    process = subprocess.Popen(
        [sys.executable, str(MCP_SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
        env=_env(home),
        creationflags=hidden_creationflags(),
    )
    try:
        assert process.stdin is not None
        process.stdin.close()
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.kill()


def test_mcp_server_exits_when_parent_process_is_killed(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    wrapper = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen(\n"
        "    [sys.executable, sys.argv[1]],\n"
        "    stdin=subprocess.PIPE,\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL,\n"
        ")\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", wrapper, str(MCP_SCRIPT)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
        env=_env(home),
        text=True,
        creationflags=hidden_creationflags(),
    )
    child_pid = 0
    try:
        assert parent.stdout is not None
        line = parent.stdout.readline().strip()
        assert line.isdigit(), line
        child_pid = int(line)
        assert process_alive(child_pid)

        parent.terminate()
        parent.wait(timeout=15)

        assert _wait_dead(child_pid, timeout=15), "MCP server became an orphan after parent death"
    finally:
        if parent.poll() is None:
            parent.kill()
        if child_pid > 0 and psutil.pid_exists(child_pid):
            try:
                psutil.Process(child_pid).kill()
            except psutil.Error:
                pass
