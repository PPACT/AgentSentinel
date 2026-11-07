"""P1 daemon：token、端点、重复 role、shutdown 回收。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tools.orchestrator.launcher import process_alive

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")

ROOT = Path(__file__).resolve().parents[2]


def _read_info(home: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((home / "daemon.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _api(
    home: Path,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    token: bool = True,
) -> tuple[int, dict[str, Any]]:
    info = _read_info(home)
    assert info is not None
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = "Bearer " + (home / "token").read_text(encoding="utf-8").strip()
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://{info['host']}:{info['port']}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status), _decode(response.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code), _decode(exc.read())


def _decode(raw: bytes) -> dict[str, Any]:
    data = json.loads(raw.decode("utf-8") or "{}")
    return data if isinstance(data, dict) else {}


def _wait_healthy(home: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _read_info(home) is not None:
            try:
                status, body = _api(home, "GET", "/health", token=False)
            except OSError:
                pass
            else:
                if status == 200 and body.get("ok"):
                    return
        time.sleep(0.1)
    raise AssertionError("daemon did not become healthy")


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[Path]:
    home = tmp_path / "orch"
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(home)
    environment["PYTHONUTF8"] = "1"
    log_path = tmp_path / "daemon.log"
    with log_path.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.orchestrator.service",
                "--serve",
                "--port",
                "0",
                "--home",
                str(home),
            ],
            cwd=ROOT,
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    try:
        _wait_healthy(home)
        yield home
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


def test_health_needs_no_token_but_other_endpoints_do(daemon: Path) -> None:
    status, body = _api(daemon, "GET", "/health", token=False)
    assert status == 200
    assert body["ok"] is True

    status, _ = _api(daemon, "GET", "/status", token=False)
    assert status == 401


def test_register_resolve_and_status(daemon: Path) -> None:
    status, body = _api(
        daemon,
        "POST",
        "/register",
        {"role": "ext", "pid": os.getpid(), "command": "external"},
    )
    assert status == 200
    assert body["agent"]["status"] == "registered"

    status, body = _api(daemon, "POST", "/resolve", {"role": "ext"})
    assert status == 200
    assert body["agent"]["pid"] == os.getpid()
    assert body["agent"]["alive"] is True

    status, body = _api(daemon, "GET", "/status")
    assert status == 200
    assert [agent["role"] for agent in body["agents"]] == ["ext"]


def test_duplicate_role_start_is_rejected(daemon: Path) -> None:
    first_status, first = _api(daemon, "POST", "/start", {"role": "dev", "dummy": True})
    assert first_status == 200
    pid = int(first["agent"]["pid"])

    second_status, second = _api(daemon, "POST", "/start", {"role": "dev", "dummy": True})
    assert second_status == 409
    assert "already running" in str(second.get("error"))
    assert process_alive(pid)


def test_stop_reclaims_worker_and_child(daemon: Path) -> None:
    status, started = _api(daemon, "POST", "/start", {"role": "dev", "dummy": True})
    assert status == 200
    pid = int(started["agent"]["pid"])
    run_info = json.loads((daemon / "run" / "dev.json").read_text(encoding="utf-8"))
    child_pid = int(run_info["child_pid"])
    assert process_alive(pid)
    assert process_alive(child_pid)

    status, stopped = _api(daemon, "POST", "/stop", {"role": "dev"})
    assert status == 200
    assert stopped["stopped"] is True
    assert stopped["reclaim"]["worker"]["alive"] is False
    assert stopped["reclaim"]["child"]["alive"] is False
    assert process_alive(pid) is False
    assert process_alive(child_pid) is False


def test_shutdown_reclaims_all_workers(daemon: Path) -> None:
    status, started = _api(daemon, "POST", "/start", {"role": "dev", "dummy": True})
    assert status == 200
    pid = int(started["agent"]["pid"])
    run_info = json.loads((daemon / "run" / "dev.json").read_text(encoding="utf-8"))
    child_pid = int(run_info["child_pid"])

    status, _ = _api(daemon, "POST", "/shutdown")
    assert status == 200

    deadline = time.monotonic() + 15
    while process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert process_alive(pid) is False
    assert process_alive(child_pid) is False
