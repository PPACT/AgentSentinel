"""P4 MCP stdio server：把编排 daemon 的 HTTP 端点暴露为 MCP 工具。

工具名统一 ``orchestrator_*``，在 Claude Code 中显示为
``mcp__<server>__orchestrator_*``。server 本身无状态，token 与 daemon 地址
每次调用时从 ``.claude/orchestrator/`` 读取。
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import signal
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil  # type: ignore[import-untyped]  # psutil 未随包提供 stubs

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    # .mcp.json 以脚本路径启动本文件，项目的包根不在 sys.path 上。
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.orchestrator.locking import MAX_WAIT_MS, FileLock, lock_file_name

SERVER_NAME = "sentinel"
SUPPORTED_PROTOCOL = "2024-11-05"
DEFAULT_HOME = PROJECT_ROOT / ".claude" / "orchestrator"
REQUEST_TIMEOUT_SECONDS = 30.0
PARENT_CHECK_SECONDS_ENV = "SENTINEL_ORCH_PARENT_CHECK_SECONDS"
DEFAULT_PARENT_CHECK_SECONDS = 2.0
PROCESS_STARTED_AT = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
CACHE_DIR_NAME = "agents"
PENDING_DIR_NAME = "pending"
_registration_lock: FileLock | None = None
_registration_lock_path: Path | None = None


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
        "description": (
            "Register this MCP server as a role (idempotent). A lock file is written to "
            "pending/ so a daemon started later discovers this process on its next scan; "
            "if the daemon is offline the result is pending (no network retry)."
        ),
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


def _log_path() -> Path:
    return orchestrator_home() / "logs" / f"mcp-server-{os.getpid()}.log"


def log_event(event: str) -> None:
    """MCP stdio 不能污染 stdout；诊断写项目内日志 + stderr。"""
    line = f"{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%S.%fZ')} pid={os.getpid()} {event}\n"
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line)
    except OSError:
        pass
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def log_exception(context: str) -> None:
    detail = traceback.format_exc().replace("\n", " | ")[:2000]
    log_event(f"exception in {context}: {detail}")


def _signal_handler(signum: int, _frame: Any) -> None:
    log_event(f"signal {signum}")
    raise SystemExit(0)


def parent_check_seconds() -> float:
    raw = os.environ.get(PARENT_CHECK_SECONDS_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return DEFAULT_PARENT_CHECK_SECONDS
        if value > 0:
            return value
    return DEFAULT_PARENT_CHECK_SECONDS


def parent_alive(
    pid: int,
    create_time: float | None,
    *,
    process_factory: Callable[[int], Any] | None = None,
) -> bool:
    """父进程是否仍存活；PID 被系统复用时按 create_time 判死。

    读不到信息时返回 True：宁可多活一轮，也不误杀自己。
    """
    if pid <= 0:
        return True
    factory = process_factory or psutil.Process
    try:
        process = factory(pid)
        if create_time is not None and abs(float(process.create_time()) - create_time) > 0.05:
            return False
        return bool(process.is_running()) and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError):
        return True


def watch_parent(
    pid: int,
    create_time: float | None,
    *,
    alive: Callable[[int, float | None], bool] = parent_alive,
    sleep: Callable[[float], None] = time.sleep,
    exit_process: Callable[[int], None] = os._exit,
    max_cycles: int | None = None,
) -> None:
    """父进程消失立即退出。

    必须在独立线程里用 ``os._exit``：``sys.exit`` 只会结束当前线程，
    主线程仍阻塞在 stdin 上，进程会变成孤儿。
    """
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        sleep(parent_check_seconds())
        cycles += 1
        if not alive(pid, create_time):
            log_event(f"parent {pid} gone; exiting")
            exit_process(0)
            return


def start_parent_watchdog() -> None:
    pid = os.getppid()
    if pid <= 0:
        return
    try:
        create_time: float | None = float(psutil.Process(pid).create_time())
    except (psutil.Error, OSError):
        create_time = None
    threading.Thread(
        target=watch_parent,
        args=(pid, create_time),
        daemon=True,
        name="orchestrator-parent-watch",
    ).start()


def _configure_stdio() -> None:
    """MCP 走 UTF-8；Windows 默认 cp936 会把 UTF-8 参数解成孤立代理字符。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


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
    """读取本 Claude Code 会话缓存的角色；--resume 复用 session id 时可恢复。"""
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


def _pending_dir() -> Path:
    return orchestrator_home() / PENDING_DIR_NAME


def release_pending_registration() -> None:
    """释放注册锁；进程退出时操作系统也会自动释放，这里用于角色切换/测试。"""
    global _registration_lock, _registration_lock_path
    lock, _registration_lock = _registration_lock, None
    _registration_lock_path = None
    if lock is not None:
        lock.release()


def hold_pending_registration(payload: dict[str, Any]) -> Path | None:
    """创建并持有 ``pending/<role>_<pid>_<create_time>.lock``。

    锁由操作系统随进程释放，Daemon 扫描时以非阻塞加锁结果判断存活；文件名携带
    全部元数据，扫描方无需读取内容（Windows 字节范围锁是强制锁）。
    """
    global _registration_lock, _registration_lock_path
    role = str(payload.get("role", "")).strip().casefold()
    try:
        create_time = float(psutil.Process(os.getpid()).create_time())
        name = lock_file_name(role, os.getpid(), create_time)
    except (psutil.Error, OSError, ValueError):
        return None
    path = _pending_dir() / name
    if (
        _registration_lock is not None
        and _registration_lock.held
        and _registration_lock_path == path
    ):
        return path
    release_pending_registration()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    lock = FileLock(path)
    try:
        acquired = lock.acquire(wait_ms=MAX_WAIT_MS)
    except OSError as exc:
        log_event(f"pending lock error: {exc}")
        return None
    if not acquired:
        log_event(f"pending lock busy after {MAX_WAIT_MS:.0f}ms; giving up")
        return None
    _registration_lock = lock
    _registration_lock_path = path
    return path


def save_registration(payload: dict[str, Any]) -> None:
    hold_pending_registration(payload)
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
    # 原子替换：mkdir 后用唯一临时文件，避免并发写同一 .tmp。
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


def auto_register() -> None:
    """有 SENTINEL_ROLE 用环境角色（窗口级兜底）；否则回退到本会话缓存（--resume）。"""
    cached = load_cached_registration()
    role = os.environ.get("SENTINEL_ROLE", "").strip()
    if not role and cached is not None:
        role = str(cached.get("role", "")).strip()
    log_event(f"auto_register role={role!r} cached={cached is not None}")
    if not role:
        log_event("auto_register skip: no role")
        return
    session = str(cached.get("session", "")) if cached is not None else ""
    ref = str(cached.get("ref", "")) if cached is not None else ""
    payload = registration_payload(role, session, ref)
    try:
        daemon_request("POST", "/register", payload)
    except (OSError, urllib.error.URLError) as exc:
        log_event(f"auto_register daemon offline: {exc}")
    # 无论 daemon 是否在线都留锁：中控后启动时由扫描立即发现，无需网络心跳。
    save_registration(payload)
    log_event(f"auto_register saved role={role} session={session!r} ref={ref!r}")


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
        if name == "orchestrator_register" and payload is not None:
            # 中控未启动时只留锁文件与缓存：daemon 起来后由扫描发现，不做网络重试。
            save_registration(payload)
            return _tool_result(
                202,
                {
                    "status": "pending",
                    "detail": "daemon offline; registration lock written",
                    "lock": _registration_lock_path.name
                    if _registration_lock_path is not None
                    else None,
                    "error": str(exc),
                },
            )
        return _tool_result(503, {"error": str(exc)})
    if name == "orchestrator_register" and payload is not None:
        if 200 <= status < 300:
            save_registration(payload)
        elif status >= 500:
            save_registration(payload)
            return _tool_result(
                202,
                {
                    "status": "pending",
                    "detail": "daemon error; registration lock written",
                    "lock": _registration_lock_path.name
                    if _registration_lock_path is not None
                    else None,
                    "error": str(body.get("error", "")),
                },
            )
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
    _configure_stdio()
    atexit.register(lambda: log_event("atexit"))
    log_event(f"start session={session_id()!r} role_env={os.environ.get('SENTINEL_ROLE', '')!r}")
    try:
        auto_register()
    except Exception:  # noqa: BLE001 - 启动失败也要留下诊断
        log_exception("auto_register")
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    start_parent_watchdog()
    log_event("stdin loop start")
    try:
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
            log_event(f"recv method={message.get('method')!r} id={message.get('id')!r}")
            try:
                response = handle_message(message)
            except Exception:  # noqa: BLE001 - 单条消息异常不应拖垮 MCP server
                log_exception(f"handle_message {message.get('method')}")
                response = {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": -32603, "message": "internal error"},
                }
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
    except (OSError, ValueError):
        log_event("stdin closed; exiting")
        return 0
    log_event("stdin EOF; exiting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
