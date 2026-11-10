"""P4 MCP server：initialize / server.discover / tools.list / tools.call / 映射。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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


def test_register_offline_returns_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_ORCH_HOME", str(tmp_path / "missing"))
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
    assert result["isError"] is True
    assert "not running" in result["content"][0]["text"]


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


def test_stdio_roundtrip() -> None:
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "server/discover"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    ]
    stdin = "\n".join(json.dumps(request) for request in requests) + "\n"
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
        creationflags=hidden_creationflags(),
    )

    assert completed.returncode == 0, completed.stderr
    responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert len(responses[2]["result"]["tools"]) == 9


def test_registration_heartbeat_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_auto_register_uses_sentinel_role(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.delenv("SENTINEL_ROLE", raising=False)
    assert mcp_server.auto_register() is None
