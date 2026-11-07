"""P4 MCP server：initialize / server.discover / tools.list / tools.call / 映射。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tools.orchestrator import mcp_server
from tools.orchestrator.launcher import hidden_creationflags

ROOT = Path(__file__).resolve().parents[2]


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
    assert len(responses[2]["result"]["tools"]) == 8
