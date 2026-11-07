"""P0-3：最小 MCP stdio server（JSON-RPC 2.0），只暴露一个 echo 工具。

由 Claude Code 通过 ``--mcp-config`` 拉起。stdout 只写协议消息；调用记录写到
环境变量 ``P0_MCP_LOG`` 指定的文件，便于探针取证。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

SERVER_NAME = "p0echo"
SUPPORTED_PROTOCOL = "2024-11-05"


def _send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _reply(message_id: Any, result: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": message_id, "result": result})


def _error(message_id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}})


def _log(entry: dict[str, Any]) -> None:
    path = os.environ.get("P0_MCP_LOG", "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _handle(message: dict[str, Any]) -> None:
    method = message.get("method")
    message_id = message.get("id")
    raw_params = message.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    _log({"method": method, "id": message_id})

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if isinstance(requested, str) else SUPPORTED_PROTOCOL
        _reply(
            message_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"},
            },
        )
    elif method == "notifications/initialized":
        return
    elif method == "tools/list":
        _reply(
            message_id,
            {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo the given message",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"message": {"type": "string"}},
                            "required": ["message"],
                        },
                    }
                ]
            },
        )
    elif method == "tools/call":
        name = params.get("name")
        raw_arguments = params.get("arguments")
        arguments: dict[str, Any] = raw_arguments if isinstance(raw_arguments, dict) else {}
        if name != "echo":
            _error(message_id, -32602, f"unknown tool: {name}")
            return
        text = str(arguments.get("message", ""))
        _reply(message_id, {"content": [{"type": "text", "text": text}], "isError": False})
    elif method == "ping":
        _reply(message_id, {})
    elif message_id is not None:
        _error(message_id, -32601, f"method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            _handle(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
