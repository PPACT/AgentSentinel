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
from tools.orchestrator.launcher import hidden_creationflags, process_alive

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
    # 关闭后台缓存整理，避免与用例内的显式 /discover 竞态。
    environment["SENTINEL_ORCH_AGENT_CACHE_SECONDS"] = "3600"
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
            creationflags=hidden_creationflags(),
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


def test_stop_rejects_external_agent(daemon: Path) -> None:
    status, _ = _api(
        daemon,
        "POST",
        "/register",
        {"role": "ext", "pid": os.getpid(), "command": "external"},
    )
    assert status == 200

    status, body = _api(daemon, "POST", "/stop", {"role": "ext"})

    assert status == 409
    assert body["reason"] == "external_agent"
    _, status_body = _api(daemon, "GET", "/status")
    agent = next(item for item in status_body["agents"] if item["role"] == "ext")
    assert agent["status"] == "registered"
    assert agent["alive"] is True


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


def test_discover_reconciles_agent_liveness(daemon: Path) -> None:
    status, _ = _api(
        daemon,
        "POST",
        "/register",
        {"role": "alive", "pid": os.getpid(), "status": "stopped"},
    )
    assert status == 200
    status, _ = _api(
        daemon,
        "POST",
        "/register",
        {"role": "dead", "pid": 999_999_999, "status": "running"},
    )
    assert status == 200

    status, body = _api(daemon, "POST", "/discover", {})
    assert status == 200
    assert body["discovered"] is True
    by_role = {item["role"]: item for item in body["agents"]}
    assert by_role["alive"]["alive"] is True
    assert by_role["alive"]["status"] == "registered"
    assert by_role["dead"]["alive"] is False
    assert by_role["dead"]["status"] == "stopped"


def test_discover_returns_wild_list_and_hides_daemon(daemon: Path) -> None:
    status, body = _api(daemon, "POST", "/discover", {})
    assert status == 200
    wild = body.get("wild")
    assert isinstance(wild, list)

    _, status_body = _api(daemon, "GET", "/status")
    daemon_pid = int(status_body["daemon"]["pid"])
    assert daemon_pid not in {int(item["pid"]) for item in wild}


def test_register_scopes_agents_to_daemon_project(daemon: Path) -> None:
    project = os.path.normcase(str(ROOT.resolve()))
    status, body = _api(
        daemon,
        "POST",
        "/register",
        {"role": "dev", "pid": os.getpid(), "project": str(ROOT)},
    )
    assert status == 200
    assert body["agent"]["project"] == project

    status, body = _api(
        daemon,
        "POST",
        "/register",
        {"role": "doc", "pid": os.getpid(), "project": "C:\\other\\project"},
    )
    assert status == 409
    assert "project mismatch" in body["error"]

    status, body = _api(daemon, "GET", "/status")
    assert status == 200
    assert body["daemon"]["project"] == project


def test_reregister_preserves_agent_started_at(daemon: Path) -> None:
    started_at = "2026-01-02T03:04:05.000000Z"
    status, body = _api(
        daemon,
        "POST",
        "/register",
        {"role": "dev", "pid": os.getpid(), "started_at": started_at},
    )
    assert status == 200
    assert body["agent"]["started_at"] == started_at

    # 心跳/自动注册重复上报但不带 started_at：保留首次值，不随 now 漂移。
    status, body = _api(
        daemon,
        "POST",
        "/register",
        {"role": "dev", "pid": os.getpid()},
    )
    assert status == 200
    assert body["agent"]["started_at"] == started_at


def test_discover_recovers_live_cache_and_prunes_dead(daemon: Path) -> None:
    cache_dir = daemon / "agents"
    cache_dir.mkdir()
    started_at = "2026-01-02T03:04:05.000000Z"
    live = {
        "session_id": "s1",
        "session": "agentsentinel-test",
        "ref": "r1",
        "role": "dev",
        "project": str(ROOT),
        "started_at": started_at,
        "mcp_pid": os.getpid(),
        "claude_pid": os.getpid(),
        "updated_at": started_at,
    }
    (cache_dir / "s1.json").write_text(json.dumps(live), encoding="utf-8")
    dead = dict(live, session_id="s2", mcp_pid=999_999_999, claude_pid=999_999_999)
    (cache_dir / "s2.json").write_text(json.dumps(dead), encoding="utf-8")

    status, body = _api(daemon, "POST", "/discover", {})
    assert status == 200
    assert body["recovered"] == 1
    assert body["pruned"] == 1
    by_role = {item["role"]: item for item in body["agents"]}
    assert by_role["dev"]["alive"] is True
    assert by_role["dev"]["pid"] == os.getpid()
    assert by_role["dev"]["started_at"] == started_at
    assert not (cache_dir / "s2.json").exists()


def test_discover_skips_other_project_cache(daemon: Path) -> None:
    cache_dir = daemon / "agents"
    cache_dir.mkdir()
    foreign = {
        "session_id": "foreign",
        "role": "dev",
        "project": "C:\\other\\project",
        "mcp_pid": os.getpid(),
        "claude_pid": os.getpid(),
    }
    path = cache_dir / "foreign.json"
    path.write_text(json.dumps(foreign), encoding="utf-8")

    status, body = _api(daemon, "POST", "/discover", {})
    assert status == 200
    assert body["pruned"] == 0
    assert path.exists()
    assert body["agents"] == []
