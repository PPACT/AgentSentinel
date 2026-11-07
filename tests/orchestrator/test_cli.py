"""P1 CLI 端到端：daemon 起停、start/status/stop、重复 role、daemon 退出回收。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from tools.orchestrator.launcher import hidden_creationflags, process_alive

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")

ROOT = Path(__file__).resolve().parents[2]


def _run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(home)
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "tools.orchestrator", *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
        creationflags=hidden_creationflags(),
    )


def _body(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    data = json.loads(result.stdout)
    return data if isinstance(data, dict) else {}


def _cleanup(home: Path) -> None:
    _run(home, "daemon", "stop")


def test_cli_end_to_end(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    try:
        daemon = _run(home, "daemon", "start")
        assert daemon.returncode == 0, daemon.stderr
        assert _body(daemon)["running"] is True

        started = _run(home, "start", "--role", "dev", "--dummy")
        assert started.returncode == 0, started.stderr
        agent = _body(started)["agent"]
        assert agent["pid"] > 0
        assert agent["pgid"] == agent["pid"]
        assert agent["job_id"]
        assert agent["alive"] is True

        duplicate = _run(home, "start", "--role", "dev", "--dummy")
        assert duplicate.returncode == 1
        assert "already running" in duplicate.stdout

        status = _run(home, "status")
        assert status.returncode == 0
        roles = [item["role"] for item in _body(status)["agents"]]
        assert roles == ["dev"]

        resolved = _run(home, "resolve", "--role", "dev")
        assert resolved.returncode == 0
        assert _body(resolved)["agent"]["pid"] == agent["pid"]

        registered = _run(home, "register", "--role", "ext", "--pid", str(os.getpid()))
        assert registered.returncode == 0
        assert _body(registered)["agent"]["role"] == "ext"

        stopped = _run(home, "stop", "--role", "dev")
        assert stopped.returncode == 0
        reclaim = _body(stopped)["reclaim"]
        assert reclaim["worker"]["alive"] is False
        assert reclaim["child"]["alive"] is False
        assert process_alive(int(agent["pid"])) is False

        after = _run(home, "status")
        assert after.returncode == 0
        by_role = {item["role"]: item for item in _body(after)["agents"]}
        assert by_role["dev"]["status"] == "stopped"
        assert by_role["dev"]["alive"] is False

        daemon_stop = _run(home, "daemon", "stop")
        assert daemon_stop.returncode == 0
        assert _body(daemon_stop)["running"] is False
    finally:
        _cleanup(home)


def test_daemon_exit_reclaims_workers(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    try:
        assert _run(home, "daemon", "start").returncode == 0
        started = _run(home, "start", "--role", "dev", "--dummy")
        assert started.returncode == 0, started.stderr
        agent = _body(started)["agent"]
        run_info = json.loads((home / "run" / "dev.json").read_text(encoding="utf-8"))
        child_pid = int(run_info["child_pid"])
        assert process_alive(int(agent["pid"]))
        assert process_alive(child_pid)

        stopped = _run(home, "daemon", "stop")
        assert stopped.returncode == 0

        deadline = time.monotonic() + 15
        while (
            process_alive(int(agent["pid"])) or process_alive(child_pid)
        ) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert process_alive(int(agent["pid"])) is False
        assert process_alive(child_pid) is False
    finally:
        _cleanup(home)


def test_cli_task_workflow(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    try:
        assert _run(home, "daemon", "start").returncode == 0

        need = _run(
            home,
            "need",
            "--from-role",
            "doc",
            "--to-role",
            "dev",
            "--intent",
            "implement",
            "--pointer",
            "docs/local/交流区/交流区.md#p2",
            "--acceptance",
            "tests pass",
            "--idempotency-key",
            "cli-k",
        )
        assert need.returncode == 0, need.stderr
        task = _body(need)["task"]
        task_id = str(task["task_id"])
        assert task["status"] == "delivered"

        ack = _run(home, "ack", "--task-id", task_id, "--role", "dev")
        assert ack.returncode == 0, ack.stderr
        assert _body(ack)["task"]["status"] == "running"

        completed = _run(
            home,
            "completed",
            "--task-id",
            task_id,
            "--role",
            "dev",
            "--artifact",
            "workspace/out.txt",
            "--cost-tokens",
            "7",
        )
        assert completed.returncode == 0, completed.stderr
        assert _body(completed)["task"]["status"] == "done"

        tasks = _run(home, "tasks", "--role", "dev")
        assert tasks.returncode == 0, tasks.stderr
        assert [item["task_id"] for item in _body(tasks)["tasks"]] == [task_id]

        detail = _run(home, "task", "--task-id", task_id)
        assert detail.returncode == 0, detail.stderr
        assert [message["type"] for message in _body(detail)["messages"]] == [
            "need",
            "ack",
            "completed",
        ]
    finally:
        _cleanup(home)
