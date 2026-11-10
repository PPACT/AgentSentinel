"""P4 MCP server：initialize / server.discover / tools.list / tools.call / 映射。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psutil  # type: ignore[import-untyped]  # psutil 未随包提供 stubs
import pytest
from tools.orchestrator import mcp_server
from tools.orchestrator.launcher import hidden_creationflags

ROOT = Path(__file__).resolve().parents[2]


def _wait_daemon(home: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _ = mcp_server.daemon_request("GET", "/status", None)
        except OSError:
            time.sleep(0.1)
            continue
        if status == 200:
            return
        time.sleep(0.1)
    raise AssertionError(f"daemon did not become healthy at {home}")


@pytest.fixture(autouse=True)
def _stop_heartbeat() -> Iterator[None]:
    yield
    mcp_server.stop_registration_heartbeat()


@pytest.fixture
def cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "cache-home"
    monkeypatch.setenv("SENTINEL_ORCH_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "test-session-1")
    monkeypatch.setenv("CLAUDE_PID", str(os.getpid()))
    monkeypatch.delenv("SENTINEL_ROLE", raising=False)
    return home


@pytest.fixture
def daemon_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
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
            creationflags=hidden_creationflags(),
        )
    monkeypatch.setenv("SENTINEL_ORCH_HOME", str(home))
    try:
        _wait_daemon(home)
        yield home
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


def _call(message: dict[str, Any]) -> dict[str, Any]:
    response = mcp_server.handle_message(message)
    assert response is not None
    return response


def test_initialize_and_discover() -> None:
    initialized = _call(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    assert initialized["result"]["protocolVersion"] == "2025-06-18"
    assert initialized["result"]["serverInfo"]["name"] == "sentinel"

    discovered = _call({"jsonrpc": "2.0", "id": "d1", "method": "server/discover"})
    assert discovered["result"]["serverInfo"]["name"] == "sentinel"
    assert "tools" in discovered["result"]["capabilities"]


def test_tools_list_contains_orchestrator_tools() -> None:
    result = _call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]
    names = {tool["name"] for tool in result["tools"]}
    assert names == {
        "orchestrator_status",
        "orchestrator_resolve",
        "orchestrator_register",
        "orchestrator_need",
        "orchestrator_ack",
        "orchestrator_completed",
        "orchestrator_heartbeat",
        "orchestrator_task",
        "orchestrator_tasks",
    }
    ack = next(tool for tool in result["tools"] if tool["name"] == "orchestrator_ack")
    assert "attempts" in ack["inputSchema"]["properties"]


def test_tool_request_ack_passes_attempts() -> None:
    method, path, payload = mcp_server.tool_request(
        "orchestrator_ack",
        {"task_id": "T-1", "role": "dev", "attempts": 2},
    )
    assert method == "POST"
    assert path == "/ack"
    assert payload == {"task_id": "T-1", "role": "dev", "attempts": 2}


def test_tool_request_need_and_tasks() -> None:
    _, path, payload = mcp_server.tool_request(
        "orchestrator_need",
        {
            "from_role": "doc",
            "to_role": "dev",
            "intent": "implement",
            "pointer": "p",
            "idempotency_key": "k",
        },
    )
    assert path == "/need"
    assert payload is not None
    assert payload["to_role"] == "dev"

    method, path, payload = mcp_server.tool_request(
        "orchestrator_tasks", {"role": "dev", "status": "done"}
    )
    assert method == "GET"
    assert path == "/tasks?role=dev&status=done"
    assert payload is None


def test_tool_request_register_uses_mcp_server_pid() -> None:
    method, path, payload = mcp_server.tool_request(
        "orchestrator_register",
        {"role": "dev", "session": "agentsentinel-xx", "ref": "abc123"},
    )
    assert method == "POST"
    assert path == "/register"
    assert payload is not None
    assert payload["role"] == "dev"
    assert payload["pid"] == os.getpid()
    assert payload["pgid"] == os.getpid()
    assert payload["status"] == "registered"
    assert payload["command"] == "mcp_server session=agentsentinel-xx ref=abc123"
    assert payload["project"] == str(mcp_server.project_root())
    assert payload["started_at"] == mcp_server.PROCESS_STARTED_AT


def test_register_is_idempotent_and_resolvable(daemon_home: Path) -> None:
    arguments = {"role": "dev", "session": "agentsentinel-xx", "ref": "abc123"}
    for _ in range(2):
        response = _call(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "orchestrator_register", "arguments": arguments},
            }
        )
        result = response["result"]
        assert result["isError"] is False
        agent = json.loads(result["content"][0]["text"])["agent"]
        assert agent["role"] == "dev"
        assert agent["pid"] == os.getpid()
        assert agent["pgid"] == os.getpid()
        assert agent["command"] == "mcp_server session=agentsentinel-xx ref=abc123"

    status, body = mcp_server.daemon_request("POST", "/resolve", {"role": "dev"})
    assert status == 200
    assert body["agent"]["pid"] == os.getpid()

    status, body = mcp_server.daemon_request("GET", "/status", None)
    assert status == 200
    assert [agent["role"] for agent in body["agents"]].count("dev") == 1


def test_register_again_updates_mcp_server_pid(daemon_home: Path) -> None:
    for pid in (111111, 222222):
        status, body = mcp_server.daemon_request(
            "POST",
            "/register",
            {
                "role": "dev",
                "pid": pid,
                "pgid": pid,
                "status": "registered",
                "command": "mcp_server session=next ref=next",
            },
        )
        assert status == 200
        assert body["agent"]["pid"] == pid

    status, body = mcp_server.daemon_request("GET", "/status", None)
    assert status == 200
    agents = [agent for agent in body["agents"] if agent["role"] == "dev"]
    assert len(agents) == 1
    assert agents[0]["pid"] == 222222
    assert agents[0]["command"] == "mcp_server session=next ref=next"


def test_register_offline_schedules_retry(cache_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_ORCH_HEARTBEAT_SECONDS", "0.05")

    def offline(
        method: str, path: str, payload: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        raise urllib.error.URLError("daemon offline")

    monkeypatch.setattr(mcp_server, "daemon_request", offline)
    response = _call(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "orchestrator_register",
                "arguments": {"role": "dev", "session": "s", "ref": "r"},
            },
        }
    )
    result = response["result"]
    assert result["isError"] is False
    body = json.loads(result["content"][0]["text"])
    assert body["status"] == "pending"

    cached = mcp_server.load_cached_registration()
    assert cached is not None
    assert cached["role"] == "dev"
    assert list((cache_env / "agents").glob("*.json"))
    assert (cache_env / "pending" / "test-session-1.json").exists()


def test_offline_registration_retries_until_success(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_ORCH_HEARTBEAT_SECONDS", "0.05")
    calls: list[dict[str, Any]] = []

    def flaky(method: str, path: str, payload: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        calls.append(payload or {})
        if len(calls) == 1:
            raise urllib.error.URLError("daemon offline")
        return 200, {"agent": {"role": (payload or {}).get("role", "")}}

    monkeypatch.setattr(mcp_server, "daemon_request", flaky)
    response = _call(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "orchestrator_register",
                "arguments": {"role": "dev", "session": "s", "ref": "r"},
            },
        }
    )
    body = json.loads(response["result"]["content"][0]["text"])
    assert body["status"] == "pending"

    deadline = time.monotonic() + 2
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(calls) >= 2
    assert calls[1]["role"] == "dev"


def test_register_server_error_schedules_retry(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_ORCH_HEARTBEAT_SECONDS", "0.05")

    def failing(
        method: str, path: str, payload: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        return 500, {"error": "boom"}

    monkeypatch.setattr(mcp_server, "daemon_request", failing)
    response = _call(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "orchestrator_register",
                "arguments": {"role": "dev", "session": "s", "ref": "r"},
            },
        }
    )
    result = response["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"])["status"] == "pending"
    assert mcp_server.load_cached_registration() is not None


def test_offline_registration_recovers_when_daemon_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "late-orch"
    monkeypatch.setenv("SENTINEL_ORCH_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "late-session")
    monkeypatch.setenv("CLAUDE_PID", str(os.getpid()))
    monkeypatch.delenv("SENTINEL_ROLE", raising=False)
    monkeypatch.setenv("SENTINEL_ORCH_HEARTBEAT_SECONDS", "0.05")

    response = _call(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "orchestrator_register",
                "arguments": {"role": "dev", "session": "late", "ref": "r"},
            },
        }
    )
    assert json.loads(response["result"]["content"][0]["text"])["status"] == "pending"

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
            creationflags=hidden_creationflags(),
        )
    try:
        _wait_daemon(home)
        deadline = time.monotonic() + 10
        agents: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            _, body = mcp_server.daemon_request("GET", "/status", None)
            raw_agents = body.get("agents")
            agents = raw_agents if isinstance(raw_agents, list) else []
            if any(agent.get("role") == "dev" and agent.get("alive") for agent in agents):
                break
            time.sleep(0.05)
        assert any(agent.get("role") == "dev" for agent in agents)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


def test_pending_pidfile_is_discovered_by_late_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "pidfile-orch"
    monkeypatch.setenv("SENTINEL_ORCH_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "pidfile-session")
    monkeypatch.setenv("CLAUDE_PID", str(os.getpid()))
    mcp_server.write_pending_registration({"role": "dev"})

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
            creationflags=hidden_creationflags(),
        )
    try:
        _wait_daemon(home)
        _, body = mcp_server.daemon_request("GET", "/status", None)
        agents = body.get("agents", [])
        assert any(
            agent.get("role") == "dev" and agent.get("pid") == os.getpid() for agent in agents
        )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


def test_unknown_method_returns_jsonrpc_error() -> None:
    response = _call({"jsonrpc": "2.0", "id": 9, "method": "nope"})
    assert response["error"]["code"] == -32601


def test_tools_call_without_daemon_is_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_ORCH_HOME", str(tmp_path / "missing"))
    response = _call(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "orchestrator_status", "arguments": {}},
        }
    )
    result = response["result"]
    assert result["isError"] is True
    assert "not running" in result["content"][0]["text"]


def test_stdio_roundtrip(tmp_path: Path) -> None:
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "server/discover"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    ]
    stdin = "\n".join(json.dumps(request) for request in requests) + "\n"
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(tmp_path / "orch")
    environment.pop("SENTINEL_ROLE", None)
    environment.pop("CLAUDE_CODE_SESSION_ID", None)
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "orchestrator" / "mcp_server.py")],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
        cwd=ROOT,
        env=environment,
        creationflags=hidden_creationflags(),
    )

    assert completed.returncode == 0, completed.stderr
    responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert len(responses[2]["result"]["tools"]) == 9
    logs = list((tmp_path / "orch" / "logs").glob("mcp-server-*.log"))
    assert logs
    text = logs[0].read_text(encoding="utf-8")
    assert "stdin loop start" in text
    assert "stdin EOF; exiting" in text


def test_registration_heartbeat_repeats(cache_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_request(
        method: str, path: str, payload: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        calls.append(payload or {})
        return 200, {}

    monkeypatch.setattr(mcp_server, "daemon_request", fake_request)
    stop = mcp_server.start_registration_heartbeat({"role": "dev", "pid": 1}, interval=0.05)
    deadline = time.monotonic() + 2
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    stop.set()
    assert len(calls) >= 2
    assert calls[0]["role"] == "dev"


def test_registration_pidfile_roundtrip(cache_env: Path) -> None:
    payload = mcp_server.registration_payload("dev", "session-a", "ref-a")
    mcp_server.save_registration(payload)

    path = cache_env / "pending" / "test-session-1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"pid", "pid_create_time", "role", "cwd"}
    assert data["pid"] == os.getpid()
    assert data["role"] == "dev"
    assert data["cwd"] == str(mcp_server.project_root())
    assert abs(float(data["pid_create_time"]) - psutil.Process(os.getpid()).create_time()) < 0.01


def test_registration_cache_roundtrip(cache_env: Path) -> None:
    payload = mcp_server.registration_payload("dev", "session-a", "ref-a")
    mcp_server.save_registration(payload)

    cached = mcp_server.load_cached_registration()
    assert cached is not None
    assert cached["role"] == "dev"
    assert cached["session"] == "session-a"
    assert cached["ref"] == "ref-a"
    assert cached["mcp_pid"] == os.getpid()
    assert cached["claude_pid"] == os.getpid()
    assert len(list((cache_env / "agents").glob("*.json"))) == 1
    assert not list((cache_env / "agents").glob("*.tmp"))


def test_registration_cache_concurrent_writes(cache_env: Path) -> None:
    payload = mcp_server.registration_payload("dev", "session-c", "ref-c")
    threads = [
        threading.Thread(target=mcp_server.save_registration, args=(payload,)) for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    cached = mcp_server.load_cached_registration()
    assert cached is not None
    assert cached["role"] == "dev"
    assert not list((cache_env / "agents").glob("*.tmp"))


def test_registration_cache_retries_busy_replace(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = mcp_server.registration_payload("dev", "session-d", "ref-d")
    real_replace = os.replace
    attempts: dict[str, int] = {}

    def flaky_replace(src: Any, dst: Any) -> None:
        key = str(dst)
        attempts[key] = attempts.get(key, 0) + 1
        if attempts[key] < 3:
            raise PermissionError("cache file is busy")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    mcp_server.save_registration(payload)

    assert sorted(attempts.values()) == [3, 3]
    assert not list((cache_env / "pending").glob("*.tmp"))
    assert mcp_server.load_cached_registration() is not None


def test_auto_register_falls_back_to_cached_role(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_server.save_registration(mcp_server.registration_payload("doc", "session-b", "ref-b"))
    calls: list[dict[str, Any]] = []

    def fake_request(
        method: str, path: str, payload: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        calls.append(payload or {})
        return 200, {}

    monkeypatch.setattr(mcp_server, "daemon_request", fake_request)
    stop = mcp_server.auto_register()
    assert stop is not None
    stop.set()
    assert calls and calls[0]["role"] == "doc"
    assert calls[0]["session"] == "session-b"

    # 新 session id 没有缓存：不自动注册。
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "test-session-2")
    assert mcp_server.auto_register() is None


def test_auto_register_uses_sentinel_role(cache_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_request(
        method: str, path: str, payload: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        calls.append(payload or {})
        return 200, {}

    monkeypatch.setattr(mcp_server, "daemon_request", fake_request)
    monkeypatch.setenv("SENTINEL_ROLE", "doc")
    stop = mcp_server.auto_register()
    assert stop is not None
    stop.set()
    assert calls and calls[0]["role"] == "doc"
    assert calls[0]["pid"] == os.getpid()
    cached = mcp_server.load_cached_registration()
    assert cached is not None
    assert cached["role"] == "doc"
    pending = cache_env / "pending" / "test-session-1.json"
    assert json.loads(pending.read_text(encoding="utf-8"))["role"] == "doc"

    # 环境变量缺失时回退到本会话缓存。
    monkeypatch.delenv("SENTINEL_ROLE", raising=False)
    fallback = mcp_server.auto_register()
    assert fallback is not None
    fallback.set()
    assert calls[-1]["role"] == "doc"


def test_auto_register_sentinel_role_offline_writes_pending(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_ROLE", "doc")
    monkeypatch.setenv("SENTINEL_ORCH_HEARTBEAT_SECONDS", "0.05")

    def offline(
        method: str, path: str, payload: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        raise urllib.error.URLError("daemon offline")

    monkeypatch.setattr(mcp_server, "daemon_request", offline)
    stop = mcp_server.auto_register()
    assert stop is not None
    stop.set()

    data = json.loads((cache_env / "pending" / "test-session-1.json").read_text(encoding="utf-8"))
    assert data["role"] == "doc"
    assert data["pid"] == os.getpid()


def test_resume_auto_register_uses_kept_cache(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模拟 --resume：旧 MCP 已死，缓存仍保留 role，新 MCP 自动注册。"""
    payload = mcp_server.registration_payload("dev", "session-resume", "ref-r")
    mcp_server.save_registration(payload)
    cache_path = cache_env / "agents" / "test-session-1.json"
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    data["mcp_pid"] = 999_999_999
    data["claude_pid"] = 999_999_999
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    pending_path = cache_env / "pending" / "test-session-1.json"
    pending_path.unlink(missing_ok=True)
    monkeypatch.delenv("SENTINEL_ROLE", raising=False)

    calls: list[dict[str, Any]] = []

    def fake_request(
        method: str, path: str, payload: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        calls.append(payload or {})
        return 200, {}

    monkeypatch.setattr(mcp_server, "daemon_request", fake_request)
    stop = mcp_server.auto_register()
    assert stop is not None
    stop.set()
    assert calls and calls[0]["role"] == "dev"
    assert calls[0]["pid"] == os.getpid()
    cached = mcp_server.load_cached_registration()
    assert cached is not None
    assert cached["mcp_pid"] == os.getpid()
    assert json.loads(pending_path.read_text(encoding="utf-8"))["role"] == "dev"
