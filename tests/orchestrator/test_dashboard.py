"""中控终端视图：横幅、agent 表、周期刷新与启动失败可见。"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from tools.orchestrator import service
from tools.orchestrator.launcher import hidden_creationflags

ROOT = Path(__file__).resolve().parents[2]


def _agent(role: str, pid: int, *, alive: bool, status: str = "registered") -> dict[str, object]:
    return {
        "role": role,
        "pid": pid,
        "status": status,
        "alive": alive,
        "last_heartbeat": "2026-11-09T00:00:00Z",
        "current_task_id": "",
    }


def test_empty_table_keeps_header_and_hint() -> None:
    rendered = service.format_agents_table([])
    assert "role" in rendered and "pid" in rendered
    assert "暂无已注册 agent" in rendered


def test_table_lists_agents_with_alive_state() -> None:
    rendered = service.format_agents_table(
        [
            _agent("dev", 1234, alive=True, status="running"),
            _agent("doc", 5678, alive=False, status="stopped"),
        ]
    )
    assert "dev" in rendered and "1234" in rendered and "running" in rendered
    assert "doc" in rendered and "5678" in rendered and "stopped" in rendered
    assert "在线" in rendered and "已停止" in rendered


def test_refresh_seconds_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(service.DASHBOARD_REFRESH_ENV, raising=False)
    assert service.dashboard_refresh_seconds() == 0.0
    monkeypatch.setenv(service.DASHBOARD_REFRESH_ENV, "0")
    assert service.dashboard_refresh_seconds() == 0.0
    monkeypatch.setenv(service.DASHBOARD_REFRESH_ENV, "2.5")
    assert service.dashboard_refresh_seconds() == 2.5
    monkeypatch.setenv(service.DASHBOARD_REFRESH_ENV, "nope")
    assert service.dashboard_refresh_seconds() == 0.0


def _wait_info(home: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (home / "daemon.json").exists():
            return
        time.sleep(0.05)
    raise AssertionError("daemon did not start")


def _spawn_service(
    home: Path, *extra: str, env_overrides: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(home)
    environment["PYTHONUTF8"] = "1"
    if env_overrides:
        environment.update(env_overrides)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tools.orchestrator.service",
            "--serve",
            "--port",
            "0",
            "--home",
            str(home),
            *extra,
        ],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=hidden_creationflags(),
    )


def _stop(process: subprocess.Popen[str]) -> tuple[str, str]:
    process.terminate()
    try:
        out, err = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate(timeout=15)
    return out, err


def test_foreground_banner_and_empty_state(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    process = _spawn_service(home)
    try:
        _wait_info(home)
        time.sleep(0.3)
    finally:
        out, err = _stop(process)
    assert "AgentSentinel 编排中控已启动" in out, (out, err)
    assert str(home) in out
    assert "worker mode:" in out
    assert "暂无已注册 agent" in out


def test_default_refresh_prints_only_startup_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(service.DASHBOARD_REFRESH_ENV, raising=False)
    home = tmp_path / "orch"
    process = _spawn_service(home)
    try:
        _wait_info(home)
        time.sleep(0.5)
    finally:
        out, err = _stop(process)
    assert out.count("已注册 agent") == 1, (out, err)


def test_periodic_refresh_repeats_snapshot(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    process = _spawn_service(home, env_overrides={"SENTINEL_ORCH_DASHBOARD_REFRESH_SECONDS": "0.2"})
    try:
        _wait_info(home)
        time.sleep(0.8)
    finally:
        out, err = _stop(process)
    assert out.count("已注册 agent") >= 2, (out, err)


def test_refresh_zero_prints_only_startup_snapshot(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    process = _spawn_service(home, env_overrides={"SENTINEL_ORCH_DASHBOARD_REFRESH_SECONDS": "0"})
    try:
        _wait_info(home)
        time.sleep(0.5)
    finally:
        out, err = _stop(process)
    assert out.count("已注册 agent") == 1, (out, err)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 端口占用错误码")
def test_start_failure_is_readable_and_nonzero(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = int(holder.getsockname()[1])
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(home)
    environment["PYTHONUTF8"] = "1"
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.orchestrator.service",
                "--serve",
                "--port",
                str(port),
                "--home",
                str(home),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
            creationflags=hidden_creationflags(),
        )
    finally:
        holder.close()
    assert completed.returncode != 0
    combined = (completed.stdout or "") + (completed.stderr or "")
    assert "failed to start" in combined.lower(), combined
    assert "Traceback" not in combined, combined


def test_output_has_no_ansi_when_redirected(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    process = _spawn_service(home, env_overrides={"SENTINEL_ORCH_DASHBOARD_REFRESH_SECONDS": "0.2"})
    try:
        _wait_info(home)
        time.sleep(0.5)
    finally:
        out, err = _stop(process)
    assert "\x1b[" not in out, (out, err)
    assert "---" in out, (out, err)


def test_no_dashboard_prints_nothing(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    process = _spawn_service(home, "--no-dashboard")
    try:
        _wait_info(home)
        time.sleep(0.4)
    finally:
        out, err = _stop(process)
    assert "编排中控已启动" not in out, (out, err)
    assert "已注册 agent" not in out, (out, err)
