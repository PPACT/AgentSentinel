"""P4 MCP stdio server：把编排 daemon 的 HTTP 端点暴露为 MCP 工具。

工具名统一 ``orchestrator_*``，在 Claude Code 中显示为
``mcp__<server>__orchestrator_*``。server 本身无状态，token 与 daemon 地址
每次调用时从 ``.claude/orchestrator/`` 读取。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SERVER_NAME = "sentinel"
SUPPORTED_PROTOCOL = "2024-11-05"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOME = PROJECT_ROOT / ".claude" / "orchestrator"
REQUEST_TIMEOUT_SECONDS = 30.0
HEARTBEAT_SECONDS_ENV = "SENTINEL_ORCH_HEARTBEAT_SECONDS"
DEFAULT_HEARTBEAT_SECONDS = 15.0
PROCESS_STARTED_AT = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
CACHE_DIR_NAME = "agents"
_heartbeat_lock = threading.Lock()
_heartbeat_stop: threading.Event | None = None
_heartbeat_payload: dict[str, Any] | None = None

TOOLS: list[dict[str, Any]] = [
    {
        "name": "orchestrator_status",
        "description": "Daemon and agent status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "orchestrator_resolve",
        "description": "Resolve a role to its registered process.",
        "inputSchema": {
            "type": "object",
            "properties": {"role": {"type": "string"}},
            "required": ["role"],
        },
    },
    {
        "name": "orchestrator_register",
        "description": "Register this MCP server as a role (idempotent).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "session": {"type": "string"},
                "ref": {"type": "string"},
            },
            "required": ["role"],
        },
    },
    {
        "name": "orchestrator_need",
        "description": "Create a task for a target role.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_role": {"type": "string"},
                "to_role": {"type": "string"},
                "intent": {"type": "string"},
                "pointer": {"type": "string"},
                "acceptance": {"type": "array", "items": {"type": "string"}},
                "idempotency_key": {"type": "string"},
                "task_id": {"type": "string"},
                "budget": {"type": "object"},
            },
            "required": ["from_role", "to_role", "intent", "pointer"],
        },
    },
    {
        "name": "orchestrator_ack",
        "description": "Acknowledge a delivered task (must include attempts).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "role": {"type": "string"},
                "attempts": {"type": "integer"},
            },
            "required": ["task_id", "role"],
        },
    },
    {
        "name": "orchestrator_completed",
        "description": "Mark a running task completed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "role": {"type": "string"},
                "artifacts": {"type": "array", "items": {"type": "string"}},
                "cost_tokens": {"type": "integer"},
            },
            "required": ["task_id", "role"],
        },
    },
    {
        "name": "orchestrator_heartbeat",
        "description": "Refresh the heartbeat of a role.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["role"],
        },
    },
    {
        "name": "orchestrator_task",
        "description": "Task detail with persisted messages.",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "orchestrator_tasks",
        "description": "List tasks, optionally filtered by role or status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    },
]


def orchestrator_home() -> Path:
    override = os.environ.get("SENTINEL_ORCH_HOME", "").strip()
    return Path(override).resolve() if override else DEFAULT_HOME


def heartbeat_interval_seconds() -> float:
    raw = os.environ.get(HEARTBEAT_SECONDS_ENV, "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_HEARTBEAT_SECONDS
    return value if value > 0 else DEFAULT_HEARTBEAT_SECONDS


def project_root() -> Path:
    override = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    return Path(override).resolve() if override else PROJECT_ROOT


def registration_payload(role: str, session: str = "", ref: str = "") -> dict[str, Any]:
    pid = os.getpid()
    return {
        "role": role,
        "pid": pid,
        "pgid": pid,
        "status": "registered",
        "command": f"mcp_server session={session} ref={ref}",
        "project": str(project_root()),
        "started_at": PROCESS_STARTED_AT,
        "session": session,
        "ref": ref,
    }


def session_id() -> str:
    return os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()


def claude_pid() -> int:
    raw = os.environ.get("CLAUDE_PID", "").strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def _cache_key(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
        return value
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _cache_file() -> Path | None:
    sid = session_id()
    if not sid:
        return None
    return orchestrator_home() / CACHE_DIR_NAME / f"{_cache_key(sid)}.json"


def load_cached_registration() -> dict[str, Any] | None:
    """读取本 Claude Code 会话上次注册的角色；窗口重置后 session id 变化即自然失效。"""
    path = _cache_file()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or str(data.get("session_id", "")) != session_id():
        return None
    role = data.get("role")
    if not isinstance(role, str) or not role.strip():
        return None
    return dict(data)


def _replace_with_retry(src: Path, dst: Path, attempts: int = 5) -> None:
    """Windows 上 daemon 正在读缓存时 replace 可能报共享冲突，短暂重试。"""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.02)


def save_registration(payload: dict[str, Any]) -> None:
    path = _cache_file()
    if path is None:
        return
    data = {
        "session_id": session_id(),
        "session": str(payload.get("session", "")),
        "ref": str(payload.get("ref", "")),
        "role": str(payload.get("role", "")),
        "project": str(payload.get("project", "")),
        "started_at": str(payload.get("started_at", "")),
        "mcp_pid": os.getpid(),
        "claude_pid": claude_pid(),
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    # 原子替换：mkdir 后用唯一临时文件，避免心跳与工具调用并发写同一 .tmp。
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    except OSError:
        return
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(data, ensure_ascii=False, indent=2))
        _replace_with_retry(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return


def start_registration_heartbeat(
    payload: dict[str, Any], interval: float | None = None
) -> threading.Event:
    """注册后周期重注册：daemon 重启/后启动时，agent 能在下一拍被重新识别。"""
    global _heartbeat_stop, _heartbeat_payload
    with _heartbeat_lock:
        _heartbeat_payload = dict(payload)
        if _heartbeat_stop is not None and not _heartbeat_stop.is_set():
            return _heartbeat_stop
        stop = threading.Event()
        _heartbeat_stop = stop
    delay = interval if interval is not None else heartbeat_interval_seconds()

    def loop() -> None:
        while not stop.wait(delay):
            current: dict[str, Any] | None = _heartbeat_payload
            if current is None:
                continue
            try:
                status, _ = daemon_request("POST", "/register", current)
            except (OSError, urllib.error.URLError):
                continue
            if 200 <= status < 300:
                save_registration(current)

    threading.Thread(target=loop, daemon=True, name="orchestrator-heartbeat").start()
    return stop


def stop_registration_heartbeat() -> None:
    global _heartbeat_stop, _heartbeat_payload
    with _heartbeat_lock:
        if _heartbeat_stop is not None:
            _heartbeat_stop.set()
        _heartbeat_stop = None
        _heartbeat_payload = None


def auto_register() -> threading.Event | None:
    """有 SENTINEL_ROLE 用环境角色；否则回退到本会话缓存中的角色。"""
    cached = load_cached_registration()
    role = os.environ.get("SENTINEL_ROLE", "").strip()
    if not role and cached is not None:
        role = str(cached.get("role", "")).strip()
    if not role:
        return None
    session = str(cached.get("session", "")) if cached is not None else ""
    ref = str(cached.get("ref", "")) if cached is not None else ""
    payload = registration_payload(role, session, ref)
    try:
        status, _ = daemon_request("POST", "/register", payload)
    except (OSError, urllib.error.URLError):
        status = 0
    if 200 <= status < 300:
        save_registration(payload)
    return start_registration_heartbeat(payload)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def tool_request(name: str, arguments: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    """把 MCP 工具调用映射为 daemon HTTP 请求（纯函数，供测试）。"""
    if name == "orchestrator_status":
        return "GET", "/status", None
    if name == "orchestrator_resolve":
        return "POST", "/resolve", {"role": _required(arguments, "role")}
    if name == "orchestrator_register":
        role = _required(arguments, "role")
        session = _optional(arguments, "session")
        ref = _optional(arguments, "ref")
        return "POST", "/register", registration_payload(role, session, ref)
    if name == "orchestrator_need":
        payload: dict[str, Any] = {
            "from_role": _required(arguments, "from_role"),
            "to_role": _required(arguments, "to_role"),
            "intent": _required(arguments, "intent"),
            "pointer": _required(arguments, "pointer"),
        }
        for key in ("acceptance", "idempotency_key", "task_id", "budget"):
            if key in arguments:
                payload[key] = arguments[key]
        return "POST", "/need", payload
    if name == "orchestrator_ack":
        payload = {
            "task_id": _required(arguments, "task_id"),
            "role": _required(arguments, "role"),
        }
        if "attempts" in arguments:
            payload["attempts"] = arguments["attempts"]
        return "POST", "/ack", payload
    if name == "orchestrator_completed":
        payload = {
            "task_id": _required(arguments, "task_id"),
            "role": _required(arguments, "role"),
        }
        for key in ("artifacts", "cost_tokens"):
            if key in arguments:
                payload[key] = arguments[key]
        return "POST", "/completed", payload
    if name == "orchestrator_heartbeat":
        payload = {"role": _required(arguments, "role")}
        if "task_id" in arguments:
            payload["task_id"] = arguments["task_id"]
        return "POST", "/heartbeat", payload
    if name == "orchestrator_task":
        return "POST", "/task", {"task_id": _required(arguments, "task_id")}
    if name == "orchestrator_tasks":
        query = "&".join(
            f"{key}={urllib.parse.quote(str(arguments[key]))}"
            for key in ("role", "status")
            if arguments.get(key)
        )
        return "GET", "/tasks" + (f"?{query}" if query else ""), None
    raise ValueError(f"unknown tool: {name}")


def _required(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _optional(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    return value.strip() if isinstance(value, str) else ""


def daemon_request(
    method: str, path: str, payload: dict[str, Any] | None
) -> tuple[int, dict[str, Any]]:
    home = orchestrator_home()
    info = _read_json(home / "daemon.json")
    if info is None:
        raise OSError("orchestrator daemon is not running")
    token = (home / "token").read_text(encoding="utf-8").strip()
    url = f"http://{info.get('host', '127.0.0.1')}:{info.get('port', 0)}{path}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return int(response.status), _decode(response.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code), _decode(exc.read())


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"error": "invalid daemon response"}
    return data if isinstance(data, dict) else {"value": data}


def _tool_result(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
        "isError": not (200 <= status < 300),
    }


def _call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    raw_arguments = params.get("arguments")
    arguments: dict[str, Any] = raw_arguments if isinstance(raw_arguments, dict) else {}
    try:
        method, path, payload = tool_request(str(name), arguments)
    except ValueError as exc:
        return _tool_result(400, {"error": str(exc)})
    try:
        status, body = daemon_request(method, path, payload)
    except (OSError, urllib.error.URLError) as exc:
        return _tool_result(503, {"error": str(exc)})
    if name == "orchestrator_register" and 200 <= status < 300 and payload is not None:
        save_registration(payload)
        start_registration_heartbeat(payload)
    return _tool_result(status, body)


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    raw_params = message.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        return _reply(
            message_id,
            {
                "protocolVersion": requested if isinstance(requested, str) else SUPPORTED_PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"},
            },
        )
    if method == "server/discover":
        return _reply(
            message_id,
            {
                "protocolVersion": SUPPORTED_PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _reply(message_id, {"tools": TOOLS})
    if method == "tools/call":
        return _reply(message_id, _call_tool(params))
    if method == "ping":
        return _reply(message_id, {})
    if message_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return None


def _reply(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def main() -> int:
    auto_register()
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        response = handle_message(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
