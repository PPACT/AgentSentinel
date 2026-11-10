"""daemon /discover 聚合 wild 候选。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path

import psutil  # type: ignore[import-untyped]  # psutil 未随包提供 stubs
import pytest
from tools.orchestrator.drivers.base import AgentCandidate
from tools.orchestrator.launcher import hidden_creationflags
from tools.orchestrator.service import Daemon

ROOT = Path(__file__).resolve().parents[2]


class _FakeDriver:
    name = "cwd"

    def __init__(self) -> None:
        self.seen_project = ""
        self.seen_excluded: set[int] = set()

    def discover(self, project: str, excluded_pids: Iterable[int] = ()) -> list[AgentCandidate]:
        self.seen_project = project
        self.seen_excluded = set(excluded_pids)
        return [
            AgentCandidate(
                pid=4242,
                ppid=1,
                cwd=project,
                cmdline="python fake_agent.py",
                create_time=1.0,
                source="cwd",
                child_pids=(4243, 4244),
            )
        ]


def test_discover_returns_wild_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    daemon = Daemon(tmp_path / "orch")
    fake = _FakeDriver()
    monkeypatch.setattr(daemon, "wild_driver", fake)

    result = daemon.discover()

    assert result["wild"] == [
        {
            "pid": 4242,
            "ppid": 1,
            "cwd": daemon.project,
            "cmdline": "python fake_agent.py",
            "create_time": 1.0,
            "source": "cwd",
            "root": True,
            "child_pids": [4243, 4244],
            "bound_role": None,
        }
    ]
    assert fake.seen_project == daemon.project
    assert os.getpid() in fake.seen_excluded


def _wait_pid(pidfile: Path, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return int(pidfile.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            time.sleep(0.05)
    raise AssertionError("wild agent did not start")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows start /b")
def test_real_project_process_appears_in_wild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_PROJECT_ROOT", str(ROOT))
    daemon = Daemon(tmp_path / "orch")
    script = tmp_path / "wild_agent.py"
    pidfile = tmp_path / "wild.pid"
    script.write_text(
        "import os, pathlib, sys, time\n"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    subprocess.run(
        [comspec, "/c", "start", "", "/b", sys.executable, str(script), str(pidfile)],
        cwd=str(ROOT),
        check=True,
        creationflags=hidden_creationflags(),
    )
    pid = _wait_pid(pidfile)
    try:
        wild = daemon.discover()["wild"]
        assert isinstance(wild, list)
        matched = [item for item in wild if item["pid"] == pid or pid in item["child_pids"]]
        assert matched, f"pid {pid} not represented in wild: {wild}"
    finally:
        try:
            psutil.Process(pid).terminate()
        except psutil.Error:
            pass
