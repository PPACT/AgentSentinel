"""CwdProcessDriver：cwd 过滤、进程树折叠、基础设施排除与 cmdline 脱敏。"""

from __future__ import annotations

from pathlib import Path

import psutil  # type: ignore[import-untyped]  # psutil 未随包提供 stubs
import pytest
from tools.orchestrator.drivers.base import mask_cmdline
from tools.orchestrator.drivers.cwd_process import (
    CwdProcessDriver,
    ProcessInfo,
    _psutil_snapshot,
)


def _proc(
    pid: int,
    ppid: int,
    cwd: Path,
    cmdline: str = "",
    create_time: float = 0.0,
) -> ProcessInfo:
    return ProcessInfo(
        pid=pid,
        ppid=ppid,
        cwd=str(cwd),
        cmdline=tuple(cmdline.split(" ")) if cmdline else (),
        create_time=create_time,
    )


def test_roots_fold_project_children(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    driver = CwdProcessDriver(
        snapshot=lambda: [
            _proc(100, 1, outside),
            _proc(101, 100, project, "python agent.py", 1.0),
            _proc(102, 101, project / "sub", "python helper.py", 2.0),
            _proc(103, 1, project, "python other.py", 3.0),
        ],
        cache_seconds=0,
    )

    candidates = driver.discover(str(project))

    assert [candidate.pid for candidate in candidates] == [101, 103]
    assert candidates[0].child_pids == (102,)
    assert candidates[0].cmdline == "python agent.py"


def test_outside_project_processes_are_ignored(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    driver = CwdProcessDriver(
        snapshot=lambda: [_proc(200, 1, outside, "python agent.py")],
        cache_seconds=0,
    )

    assert driver.discover(str(project)) == []


def test_child_outside_project_is_not_counted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    driver = CwdProcessDriver(
        snapshot=lambda: [
            _proc(401, 1, project, "python agent.py", 1.0),
            _proc(402, 401, outside, "python helper.py", 2.0),
        ],
        cache_seconds=0,
    )

    candidates = driver.discover(str(project))

    assert [(candidate.pid, candidate.child_pids) for candidate in candidates] == [(401, ())]


def test_excluded_pid_and_subtree_are_hidden(tmp_path: Path) -> None:
    project = tmp_path / "project"
    driver = CwdProcessDriver(
        snapshot=lambda: [
            _proc(500, 1, project, "claude.exe", 1.0),
            _proc(501, 500, project, "python mcp_server.py", 2.0),
        ],
        cache_seconds=0,
    )

    assert driver.discover(str(project), excluded_pids={501}) == []


def test_unrelated_root_survives_exclusion(tmp_path: Path) -> None:
    project = tmp_path / "project"
    driver = CwdProcessDriver(
        snapshot=lambda: [
            _proc(600, 1, project, "daemon.exe", 1.0),
            _proc(601, 600, project, "python worker.py", 2.0),
            _proc(602, 1, project, "python wild_agent.py", 3.0),
        ],
        cache_seconds=0,
    )

    candidates = driver.discover(str(project), excluded_pids={600})

    assert [candidate.pid for candidate in candidates] == [602]


@pytest.mark.parametrize(
    "cmdline",
    [
        "python tools/orchestrator/service.py --serve",
        "python tools\\orchestrator\\mcp_server.py",
        "python -m tools.orchestrator.service --serve",
        "python -m tools.orchestrator.mcp_server",
        "agentsentinel-console.exe",
        "node C:\\app\\node_modules\\vite\\bin\\vite.js",
        "cargo run --release",
    ],
)
def test_infrastructure_subtree_is_hidden(tmp_path: Path, cmdline: str) -> None:
    project = tmp_path / "project"
    driver = CwdProcessDriver(
        snapshot=lambda: [
            _proc(700, 1, project, "python host.py", 1.0),
            _proc(701, 700, project, cmdline, 2.0),
        ],
        cache_seconds=0,
    )

    assert driver.discover(str(project)) == []


def test_snapshot_error_returns_empty(tmp_path: Path) -> None:
    def broken() -> list[ProcessInfo]:
        raise psutil.AccessDenied()

    driver = CwdProcessDriver(snapshot=broken, cache_seconds=0)

    assert driver.discover(str(tmp_path)) == []


def test_results_are_cached(tmp_path: Path) -> None:
    project = tmp_path / "project"
    calls: list[int] = []

    def snapshot() -> list[ProcessInfo]:
        calls.append(1)
        return [_proc(800, 1, project, "python agent.py", 1.0)]

    driver = CwdProcessDriver(snapshot=snapshot, cache_seconds=60)
    assert [candidate.pid for candidate in driver.discover(str(project))] == [800]
    assert [candidate.pid for candidate in driver.discover(str(project))] == [800]
    assert len(calls) == 1

    driver.discover(str(project), excluded_pids={999})
    assert len(calls) == 2


class _FakeProcess:
    def __init__(self, pid: int, cwd: Path, *, deny_cwd: bool = False) -> None:
        self.pid = pid
        self._cwd = cwd
        self._deny_cwd = deny_cwd

    def ppid(self) -> int:
        return 1

    def cwd(self) -> str:
        if self._deny_cwd:
            raise psutil.AccessDenied()
        return str(self._cwd)

    def cmdline(self) -> list[str]:
        return ["python", "agent.py"]

    def create_time(self) -> float:
        return 1.0


def test_psutil_snapshot_skips_inaccessible_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = _FakeProcess(900, tmp_path)
    bad = _FakeProcess(901, tmp_path, deny_cwd=True)
    monkeypatch.setattr(psutil, "process_iter", lambda: [good, bad])

    entries = _psutil_snapshot()

    assert [entry.pid for entry in entries] == [900]


def test_cmdline_secrets_are_masked() -> None:
    masked = mask_cmdline(
        "python agent.py --token=abc123 --api-key XYZ API_KEY=synthetic Bearer tok.en"
    )

    for secret in ("abc123", "XYZ", "synthetic", "tok.en"):
        assert secret not in masked
    assert masked.count("***") >= 4


def test_cmdline_is_truncated() -> None:
    masked = mask_cmdline("x" * 500)

    assert len(masked) == 300
    assert masked.endswith("…")
