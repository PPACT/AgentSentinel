"""P1 最小 daemon：``127.0.0.1`` + token，常驻持有 Job 句柄与 registry。

CLI 只是 HTTP 客户端；daemon 退出（正常或崩溃）时 Job 句柄关闭，
``KILL_ON_JOB_CLOSE`` 会回收全部 worker 与子孙进程。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import shlex
import signal
import socket
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import parse_qs, urlparse

import psutil  # type: ignore[import-untyped]  # psutil 未随包提供 stubs

from tools.orchestrator.chat import PROMPT_MAX_CHARS, ChatBusy, ChatManager
from tools.orchestrator.drivers.cwd_process import CwdProcessDriver, _normalize_path, _within
from tools.orchestrator.engines import ENGINES, adapter_for, bracketed_paste, preflight
from tools.orchestrator.launcher import (
    Launcher,
    kill_process_tree,
    process_alive,
    sweep_leftover_real_workers,
)
from tools.orchestrator.pty_manager import PtyManager
from tools.orchestrator.registry import AgentRecord, Registry, utc_now
from tools.orchestrator.router import (
    InvalidTransition,
    RoleBudgetExceeded,
    RoleBusy,
    RoleMismatch,
    Router,
    TaskNotFound,
    TaskStore,
    ack_timeout_seconds,
    lost_timeout_seconds,
    watchdog_seconds,
)
from tools.orchestrator.transcript import (
    USAGE_KEYS,
    newest_session,
    read_appended,
    read_history,
    transcript_path,
)
from tools.orchestrator.workspaces import WorkspaceStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOST = "127.0.0.1"
_ROLE_PATTERN = re.compile(r"^[a-z0-9_-]+$")
DASHBOARD_REFRESH_ENV = "SENTINEL_ORCH_DASHBOARD_REFRESH_SECONDS"
DEFAULT_DASHBOARD_REFRESH_SECONDS = 0.0
SSE_PING_ENV = "SENTINEL_ORCH_SSE_PING_SECONDS"
DEFAULT_SSE_PING_SECONDS = 20.0
WATCH_DEBOUNCE_SECONDS = 0.15
LOG_TAIL_DEFAULT = 200
LOG_TAIL_MAX = 1000
LOG_POLL_SECONDS = 0.3
LOG_CHUNK_BYTES = 64 * 1024
REPORT_LINE_MAX = 4000
AGENT_CACHE_INTERVAL_ENV = "SENTINEL_ORCH_AGENT_CACHE_SECONDS"
DEFAULT_AGENT_CACHE_SECONDS = 30.0
AGENT_CACHE_DIR = "agents"
INSTANCE_LOCK_NAME = "daemon.lock"
SWEEP_WORKERS_ENV = "SENTINEL_ORCH_SWEEP_WORKERS"
WILD_CLEANUP_ALLOWED_EXES = frozenset({"node.exe", "python.exe", "pythonw.exe"})

_DEMO_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>AgentSentinel Chat PoC</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font: 14px/1.6 Inter, system-ui, sans-serif; background: #09090b; color: #e4e4e7; }
  header { padding: 10px 16px; border-bottom: 1px solid #27272a; display: flex; gap: 12px; align-items: center; }
  header strong { color: #38bdf8; }
  #session { color: #a1a1aa; font-family: ui-monospace, monospace; font-size: 12px; }
  main { padding: 16px; display: flex; flex-direction: column; gap: 8px; height: calc(100vh - 170px); overflow-y: auto; }
  .msg { max-width: 860px; padding: 8px 12px; border-radius: 8px; white-space: pre-wrap; word-break: break-word; }
  .user { align-self: flex-end; background: #1e3a5f; }
  .assistant { background: #18181b; border: 1px solid #27272a; }
  .thinking { background: #141417; border-left: 3px solid #52525b; color: #a1a1aa; font-size: 12px; }
  .tool { background: #132a1f; border-left: 3px solid #22c55e; color: #86efac; font-family: ui-monospace, monospace; font-size: 12px; }
  .meta { color: #71717a; font-size: 12px; }
  .error { background: #3f1d1d; color: #fca5a5; }
  footer { position: fixed; bottom: 0; left: 0; right: 0; padding: 12px 16px; background: #0c0c0f; border-top: 1px solid #27272a; display: flex; gap: 8px; }
  textarea { flex: 1; resize: none; height: 54px; background: #18181b; color: inherit; border: 1px solid #3f3f46; border-radius: 8px; padding: 8px; font: inherit; }
  button { padding: 0 18px; border-radius: 8px; border: 1px solid #0ea5e9; background: #0c4a6e; color: #e0f2fe; cursor: pointer; }
  button:disabled { opacity: .5; cursor: wait; }
</style>
</head>
<body>
<header>
  <strong>AgentSentinel Chat PoC</strong>
  <label class="meta">role <input id="role" value="dev" style="width:5rem;background:#18181b;color:#e4e4e7;border:1px solid #3f3f46;border-radius:4px;padding:1px 6px;font-family:ui-monospace,monospace"></label>
  <span id="session" class="meta">session: -</span>
  <span class="meta">每轮按角色复用 session</span>
</header>
<main id="messages"></main>
<footer>
  <textarea id="prompt" placeholder="输入内容，Ctrl+Enter 发送"></textarea>
  <button id="send">发送</button>
</footer>
<script>
const TOKEN = __TOKEN__;
const messages = document.getElementById("messages");
const promptBox = document.getElementById("prompt");
const sendButton = document.getElementById("send");
const sessionEl = document.getElementById("session");
const roleInput = document.getElementById("role");
let busy = false;

function add(kind, text) {
  const div = document.createElement("div");
  div.className = "msg " + kind;
  div.textContent = text;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

function renderEvent(raw) {
  let event;
  try { event = JSON.parse(raw); } catch { add("meta", raw); return; }
  if (event.type === "system") {
    add("meta", "初始化 session=" + event.session_id + " model=" + (event.model || "?"));
    return;
  }
  if (event.type === "assistant") {
    for (const block of (event.message && event.message.content) || []) {
      if (block.type === "text") add("assistant", block.text);
      else if (block.type === "thinking") add("thinking", "💭 " + (block.thinking || "（思考中）"));
      else if (block.type === "tool_use") add("tool", "⚙️ " + block.name + " " + JSON.stringify(block.input || {}).slice(0, 200));
    }
    return;
  }
  if (event.type === "user") {
    for (const block of (event.message && event.message.content) || []) {
      if (block.type === "tool_result") add("tool", "✅ 工具返回");
    }
    return;
  }
  if (event.type === "result") {
    add("meta", "本轮结束 subtype=" + event.subtype + " cost=$" + (event.total_cost_usd || 0).toFixed(4));
  }
}

async function send() {
  const prompt = promptBox.value.trim();
  if (!prompt || busy) return;
  add("user", prompt);
  promptBox.value = "";
  busy = true;
  sendButton.disabled = true;
  try {
    const payload = { role: (roleInput.value.trim() || "dev"), prompt: prompt };
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + TOKEN },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      add("error", "HTTP " + response.status + " " + (await response.text()));
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      let index;
      while ((index = buffer.indexOf("\\n\\n")) >= 0) {
        const frame = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        let event = "message";
        let data = "";
        for (const line of frame.split("\\n")) {
          if (line.startsWith("event: ")) event = line.slice(7);
          else if (line.startsWith("data: ")) data += line.slice(6);
        }
        if (event === "message") renderEvent(data);
        else if (event === "end") {
          const summary = JSON.parse(data);
          sessionEl.textContent = "session: " + (summary.session_id || "-") + " (role " + (summary.role || "?") + ")";
          add("meta", "summary role=" + summary.role + " resumed=" + summary.resumed + " cost=$" + (summary.cost_usd || 0).toFixed(4));
        }
      }
    }
  } catch (error) {
    add("error", String(error));
  } finally {
    busy = false;
    sendButton.disabled = false;
  }
}

sendButton.addEventListener("click", send);
promptBox.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    send();
  }
});
</script>
</body>
</html>
"""
AGENT_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
PENDING_DIR = "pending"
PENDING_CLEANUP_THRESHOLD = 100
PID_CREATE_TIME_TOLERANCE = 0.01


def agent_cache_seconds() -> float:
    raw = os.environ.get(AGENT_CACHE_INTERVAL_ENV, "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_AGENT_CACHE_SECONDS
    return value if value > 0 else DEFAULT_AGENT_CACHE_SECONDS


class ProjectMismatch(ValueError):
    """agent 上报的项目与 daemon 所属项目不一致。"""


def _normalize_project(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).resolve()))


def derive_project(home: Path) -> str:
    """daemon 所属项目：显式 env 优先；默认取 <项目>/.claude/orchestrator 的祖父目录。"""
    override = os.environ.get("SENTINEL_PROJECT_ROOT", "").strip()
    if override:
        return _normalize_project(override)
    resolved = home.resolve()
    if resolved.name == "orchestrator" and resolved.parent.name == ".claude":
        return _normalize_project(resolved.parent.parent)
    return _normalize_project(PROJECT_ROOT)


def workspace_db_path(home: Path) -> Path:
    """配置中心 DB：默认中控根目录（跨工作区共享）；隔离测试用独立 home。"""
    override = os.environ.get("SENTINEL_WORKSPACES_DB", "").strip()
    if override:
        return Path(override)
    resolved = home.resolve()
    default_home = (PROJECT_ROOT / ".claude" / "orchestrator").resolve()
    if resolved == default_home:
        return PROJECT_ROOT / ".claude" / "workspaces.sqlite3"
    return home / "workspaces.sqlite3"


def _configure_stdio(*streams: Any) -> None:
    """daemon 入口：编码失败时用替换字符，避免日志写入中断或产生乱码。"""
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (OSError, ValueError):
            continue


def dashboard_refresh_seconds() -> float:
    raw = os.environ.get(DASHBOARD_REFRESH_ENV, "").strip()
    if not raw:
        return DEFAULT_DASHBOARD_REFRESH_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_DASHBOARD_REFRESH_SECONDS


def sse_ping_seconds() -> float:
    raw = os.environ.get(SSE_PING_ENV, "").strip()
    if not raw:
        return DEFAULT_SSE_PING_SECONDS
    try:
        return max(0.1, float(raw))
    except ValueError:
        return DEFAULT_SSE_PING_SECONDS


def worker_mode() -> str:
    return os.environ.get("SENTINEL_ORCH_WORKER_MODE", "dummy").strip().lower() or "dummy"


def format_agents_table(agents: list[dict[str, Any]]) -> str:
    header = (
        f"{'role':<10} {'pid':<8} {'status':<12} {'alive':<6} "
        f"{'last_heartbeat':<26} current_task_id"
    )
    lines = [header]
    if not agents:
        lines.append("暂无已注册 agent")
        return "\n".join(lines)
    for agent in agents:
        alive = "在线" if agent.get("alive") else "已停止"
        lines.append(
            f"{agent.get('role', '')!s:<10} {agent.get('pid', '')!s:<8} "
            f"{agent.get('status', '')!s:<12} {alive:<6} "
            f"{agent.get('last_heartbeat') or '-'!s:<26} "
            f"{agent.get('current_task_id') or '-'!s}"
        )
    return "\n".join(lines)


def format_banner(daemon: Daemon, port: int) -> str:
    return "\n".join(
        [
            "AgentSentinel 编排中控已启动",
            f"  pid:         {os.getpid()}",
            f"  listen:      {daemon.host}:{port}",
            f"  home:        {daemon.home}",
            f"  worker mode: {worker_mode()}",
            f"  started at:  {daemon.started_at}",
        ]
    )


def render_dashboard(daemon: Daemon, port: int) -> str:
    agents: list[dict[str, Any]] = daemon.status()["agents"]
    return format_banner(daemon, port) + "\n\n" + format_agents_table(agents)


def supports_ansi_clear() -> bool:
    """终端支持 ANSI 清屏时返回 True；否则降级为分隔线重打印。"""
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enabled = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if mode.value & enabled:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | enabled))
    except (AttributeError, OSError):
        return False


def _render_agents_update(snapshot: dict[str, Any], clear: bool) -> None:
    if clear:
        sys.stdout.write("\033[2J\033[H")
    else:
        sys.stdout.write("\n" + "-" * 60 + "\n")
    sys.stdout.write(f"[{utc_now()}] 已注册 agent\n")
    sys.stdout.write(format_agents_table(snapshot["agents"]) + "\n")
    sys.stdout.flush()


def _dashboard_loop(daemon: Daemon, port: int, refresh: float, stop: threading.Event) -> None:
    del port  # 端口只在横幅里展示；周期刷新只重绘 agents 表
    clear = supports_ansi_clear()
    while not stop.wait(refresh):
        _render_agents_update(daemon.status(), clear)


def _agents_projection(snapshot: dict[str, Any]) -> tuple[tuple[str, str, bool, str], ...]:
    return tuple(
        sorted(
            (
                str(agent.get("role", "")),
                str(agent.get("status", "")),
                bool(agent.get("alive")),
                str(agent.get("current_task_id") or ""),
            )
            for agent in snapshot.get("agents", [])
        )
    )


def _watch_loop(daemon: Daemon, stop: threading.Event, show_heartbeat: bool) -> None:
    clear = supports_ansi_clear()
    previous = _agents_projection(daemon.status())
    last_seen = daemon.revision
    while not stop.is_set():
        revision = daemon.wait_for_change(last_seen)
        if stop.is_set():
            break
        if revision == last_seen:
            continue
        snapshot = daemon.status()
        projection = _agents_projection(snapshot)
        last_seen = revision
        if show_heartbeat or projection != previous:
            _render_agents_update(snapshot, clear)
            previous = projection


def _offer_latest(target: queue.Queue[dict[str, Any]], frame: dict[str, Any]) -> None:
    """订阅者队列只保留最新帧：慢客户端不阻塞发布方。"""
    try:
        target.put_nowait(frame)
        return
    except queue.Full:
        pass
    try:
        target.get_nowait()
    except queue.Empty:
        pass
    try:
        target.put_nowait(frame)
    except queue.Full:
        pass


def _decode_log_line(raw: bytes) -> str:
    """日志行解码：UTF-8 优先；兼容旧版 cp936/GBK 日志；最后用替换字符兜底。"""
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _split_log_chunk(pending: bytes, chunk: bytes) -> tuple[list[str], bytes]:
    """按换行切分日志块，返回已解码的完整行与未完成的尾行字节。"""
    parts = (pending + chunk).split(b"\n")
    rest = parts.pop()
    return [_decode_log_line(line.rstrip(b"\r")) for line in parts], rest


def _tail_lines(path: Path, limit: int) -> list[str]:
    if limit <= 0:
        return []
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            read_size = min(size, LOG_CHUNK_BYTES)
            stream.seek(size - read_size)
            data = stream.read(read_size)
    except OSError:
        return []
    lines = data.split(b"\n")
    if read_size < size and lines:
        # 读窗口从文件中间开始，首段是残行，丢弃以免按 GBK 误判。
        lines.pop(0)
    if lines and lines[-1] == b"":
        lines.pop()
    return [_decode_log_line(line.rstrip(b"\r")) for line in lines][-limit:]


def default_home() -> Path:
    override = os.environ.get("SENTINEL_ORCH_HOME", "").strip()
    if override:
        return Path(override).resolve()
    return PROJECT_ROOT / ".claude" / "orchestrator"


def load_or_create_token(home: Path) -> str:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "token"
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if not token:
        token = secrets.token_hex(24)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(token + "\n", encoding="utf-8")
        os.replace(temp, path)
    return token


def write_daemon_info(home: Path, info: dict[str, Any]) -> None:
    path = home / "daemon.json"
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def _read_daemon_info(home: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((home / "daemon.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _port_in_use(host: str, port: int, timeout: float = 0.3) -> bool:
    """固定端口启动前的占用检测；端口 0（临时端口）不检查。"""
    if port <= 0:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _current_process_identity() -> tuple[int, float]:
    pid = os.getpid()
    try:
        created = float(psutil.Process(pid).create_time())
    except (psutil.Error, OSError):
        created = 0.0
    return pid, created


def _lock_holder_alive(path: Path) -> bool:
    """锁文件记录 pid + create_time；PID 被复用（create_time 不同）视为残留。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    pid = data.get("pid")
    created = data.get("create_time")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if isinstance(created, bool) or not isinstance(created, (int, float)) or created <= 0:
        return False
    try:
        process = psutil.Process(pid)
        return bool(
            abs(process.create_time() - float(created)) <= 0.05
            and process.is_running()
            and process.status() != psutil.STATUS_ZOMBIE
        )
    except (psutil.Error, OSError):
        return False


def _acquire_instance_lock(home: Path) -> bool:
    """同一 home 只允许一个 daemon；残留锁按 pid + create_time 判定后回收。"""
    home.mkdir(parents=True, exist_ok=True)
    path = home / INSTANCE_LOCK_NAME
    pid, created = _current_process_identity()
    payload = json.dumps({"pid": pid, "create_time": created})
    for _ in range(3):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _lock_holder_alive(path):
                return False
            try:
                path.unlink()
            except OSError:
                return False
            continue
        try:
            os.write(handle, payload.encode("ascii"))
        finally:
            os.close(handle)
        return True
    return False


def _release_instance_lock(home: Path) -> None:
    path = home / INSTANCE_LOCK_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict) or data.get("pid") != os.getpid():
        return
    try:
        path.unlink()
    except OSError:
        return


def _pty_environment() -> dict[str, str]:
    """PTY 里必须是独立的 claude 会话，不能继承外层 Claude Code 的子会话标记。"""
    env = os.environ.copy()
    for key in list(env):
        if (
            key in {"CLAUDECODE", "CLAUDE_PID"}
            or key.startswith("CLAUDE_CODE_")
            and key != "CLAUDE_CODE_GIT_BASH_PATH"
        ):
            env.pop(key, None)
    return env


def _should_sweep_workers() -> bool:
    """pytest 进程内禁用真实清扫，避免测试误杀宿主机进程。"""
    if os.environ.get(SWEEP_WORKERS_ENV, "1").strip() == "0":
        return False
    return not os.environ.get("PYTEST_CURRENT_TEST")


def _role(payload: dict[str, Any]) -> str:
    value = payload.get("role")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("role is required")
    role = value.strip().casefold()
    if _ROLE_PATTERN.fullmatch(role) is None:
        raise ValueError(f"invalid role: {role}")
    return role


def _agent_id(record: AgentRecord) -> str:
    """注册期唯一标识：项目 + 角色 + pid + started_at，角色重开/换进程即变化。"""
    raw = f"{record.project}|{record.role}|{record.pid}|{record.started_at}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _optional_role(payload: dict[str, Any]) -> str:
    value = payload.get("role")
    return value.strip().casefold() if isinstance(value, str) else ""


def _optional_agent_id(payload: dict[str, Any]) -> str:
    value = payload.get("agent_id")
    return value.strip() if isinstance(value, str) else ""


DEFAULT_TOKEN_BUDGET = 200_000
DEFAULT_HANDOFF_PROMPT = "上下文即将满，请立即更新 STATUS 并准备交接。"
DEFAULT_HANDOFF_TIMEOUT_SECONDS = 180.0
HANDOFF_RELOCATE_SECONDS = 20.0


def handoff_prompt() -> str:
    return os.environ.get("SENTINEL_HANDOFF_PROMPT", "").strip() or DEFAULT_HANDOFF_PROMPT


def handoff_timeout_seconds() -> float:
    raw = os.environ.get("SENTINEL_HANDOFF_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_HANDOFF_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_HANDOFF_TIMEOUT_SECONDS


def resolve_wake_prompt(workspace: dict[str, Any]) -> str:
    file_text = str(workspace.get("wake_prompt_file") or "").strip()
    if file_text:
        try:
            text = Path(file_text).expanduser().read_text(encoding="utf-8")[:65536]
        except OSError as exc:
            raise ValueError(f"读取唤醒提示词文件失败：{exc}") from exc
        if text.strip():
            return text.strip()
    return str(workspace.get("wake_prompt") or "").strip()


def _transcript_offset(project: str, session: str) -> int:
    path = transcript_path(project, session)
    if path is None:
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _inject_prompt(pty: Any, text: str) -> None:
    payload = bracketed_paste(text) if "\n" in text else text
    pty.write(payload + "\r")


def _query_role(query: dict[str, list[str]]) -> str:
    value = (query.get("role") or [""])[0]
    return value.strip().casefold() if isinstance(value, str) else ""


def _query_agent_id(query: dict[str, list[str]]) -> str:
    value = (query.get("agent_id") or [""])[0]
    return value.strip() if isinstance(value, str) else ""


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
    except OSError:
        return False
    return True


def _cache_expired(data: dict[str, Any], path: Path) -> bool:
    """角色缓存只在 TTL 到期后删除；进程死亡不删，留给 --resume 复用。"""
    raw = _str(data.get("updated_at"))
    timestamp: datetime | None = None
    if raw:
        try:
            timestamp = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        except ValueError:
            timestamp = None
    if timestamp is None:
        try:
            timestamp = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        except OSError:
            return False
    return datetime.now(UTC) - timestamp > timedelta(seconds=AGENT_CACHE_TTL_SECONDS)


class Daemon:
    def __init__(self, home: Path, host: str = DEFAULT_HOST, port: int = 0) -> None:
        self.home = home
        self.project = derive_project(home)
        self.host = host
        self.port = port
        self.token = load_or_create_token(home)
        self.registry = Registry(home / "registry.sqlite3")
        self.launcher = Launcher(home)
        self.store = TaskStore(home / "registry.sqlite3")
        self.router = Router(
            store=self.store,
            registry=self.registry,
            launcher=self.launcher,
            token=self.token,
            project=self.project,
        )
        self.wild_driver = CwdProcessDriver()
        self.chat = ChatManager(external_session=self.external_session)
        pty_override = os.environ.get("SENTINEL_PTY_COMMAND", "").strip()
        self._pty_command = tuple(shlex.split(pty_override)) if pty_override else None
        self._pty_session_override = os.environ.get("SENTINEL_PTY_SESSION_ID", "").strip() or None
        self._pty_env = _pty_environment()
        self._pty_lock = threading.Lock()
        self._pty_sessions: dict[str, str] = {}
        self.workspaces = WorkspaceStore(workspace_db_path(home))
        self.workspaces.seed_default(self.project)
        self.pty = self._new_pty("", self._pty_session_override)
        self._handoff_lock = threading.Lock()
        self._handoff: dict[str, Any] = {"active": False, "phase": "idle", "message": ""}
        self.ack_timeout = ack_timeout_seconds()
        self.lost_seconds = lost_timeout_seconds()
        self.watchdog_seconds = watchdog_seconds()
        self._last_watchdog = 0.0
        self._last_agent_cache = 0.0
        self.server: ThreadingHTTPServer | None = None
        self.started_at = utc_now()
        self._stopping = threading.Event()
        self._stop_monitor = threading.Event()
        self._stop_dashboard = threading.Event()
        self._revision = 0
        self._condition = threading.Condition()
        self._subscribers: list[queue.Queue[dict[str, Any]]] = []
        self.router.startup_recovery()
        self.reconcile_pending_agents()

    def active_workspace(self) -> dict[str, Any]:
        workspace = self.workspaces.active()
        if workspace is None:
            workspace = self.workspaces.seed_default(self.project)
        return workspace

    def workspace_budget(self) -> int:
        raw = os.environ.get("SENTINEL_TOKEN_BUDGET", "").strip()
        if raw:
            try:
                value = int(raw)
            except ValueError:
                value = 0
            if value > 0:
                return value
        workspace = self.active_workspace()
        try:
            value = int(workspace.get("budget_tokens") or DEFAULT_TOKEN_BUDGET)
        except (TypeError, ValueError):
            value = DEFAULT_TOKEN_BUDGET
        return max(1, value)

    def transcript_project(self) -> str:
        return str(self.active_workspace().get("project_path") or self.project)

    def pty_preflight(self) -> dict[str, Any]:
        workspace = self.active_workspace()
        project = str(workspace.get("project_path") or self.project)
        if self._pty_command:
            return preflight(" ".join(self._pty_command), project)
        return preflight(str(workspace.get("command") or ""), project)

    def reset_pty(self) -> None:
        with self._pty_lock:
            if self.pty.status().get("running"):
                self.pty.stop()
            self._pty_sessions.clear()

    def activate_workspace(self, workspace_id: int) -> dict[str, Any]:
        workspace = self.workspaces.activate(workspace_id)
        self.reset_pty()
        self._publish()
        return workspace

    def _new_pty(self, role: str, session_id: str | None = None, agent_id: str = "") -> PtyManager:
        workspace = self.active_workspace()
        adapter = adapter_for(str(workspace.get("engine") or "claude"))
        command_text = str(workspace.get("command") or adapter.default_command)
        factory = (
            None if self._pty_command else lambda sid: adapter.build_command(command_text, sid)
        )
        return PtyManager(
            cwd=str(workspace.get("project_path") or self.project),
            command=self._pty_command,
            command_factory=factory,
            session_id=session_id,
            env=self._pty_env,
            state_path=self.home / "pty.json",
            role=role,
            agent_id=agent_id,
            engine=adapter.id,
        )

    def handoff_status(self) -> dict[str, Any]:
        with self._handoff_lock:
            return dict(self._handoff)

    def _handoff_phase(self, phase: str, message: str) -> None:
        with self._handoff_lock:
            self._handoff.update({"phase": phase, "message": message})

    def start_handoff(self, role: str, agent_id: str) -> tuple[int, dict[str, Any]]:
        workspace = self.active_workspace()
        adapter = adapter_for(str(workspace.get("engine") or "claude"))
        if not adapter.supports_clear:
            return 400, {"error": "当前引擎不支持一键交接", "reason": "handoff_unsupported"}
        try:
            wake = resolve_wake_prompt(workspace)
        except ValueError as exc:
            return 400, {"error": str(exc), "reason": "wake_prompt_read_failed"}
        if not wake:
            return 400, {
                "error": "请先在设置/工作区配置唤醒提示词",
                "reason": "wake_prompt_required",
            }
        pty = self.pty
        if not pty.status().get("running"):
            return 409, {"error": "PTY 未运行，无法交接", "reason": "pty_not_running"}
        session = pty.session_id
        project = self.transcript_project()
        offset = _transcript_offset(project, session)
        with self._handoff_lock:
            if self._handoff.get("active"):
                return 409, {"error": "已有交接进行中", "reason": "handoff_busy"}
            self._handoff = {
                "active": True,
                "phase": "handoff",
                "message": "正在注入交接提示词…",
                "role": role,
                "agent_id": agent_id,
                "session_before": session,
                "session_after": "",
                "started_at": utc_now(),
                "finished_at": "",
                "error": "",
                "warning": "",
            }
        threading.Thread(
            target=self._run_handoff,
            args=(role, session, project, offset, wake),
            daemon=True,
        ).start()
        return 202, self.handoff_status()

    def _run_handoff(self, role: str, session: str, project: str, offset: int, wake: str) -> None:
        pty = self.pty
        try:
            _inject_prompt(pty, handoff_prompt())
            self._handoff_phase("waiting", "等待 Agent 完成…")
            if not self._wait_handoff_reply(pty, project, session, offset):
                raise RuntimeError("等待 Agent 完成超时")
            self._handoff_phase("clearing", "正在清空会话…")
            clear_at = time.time()
            pty.write("/clear\r")
            time.sleep(0.8)
            self._handoff_phase("wake", "正在注入唤醒提示词…")
            _inject_prompt(pty, wake)
            self._handoff_phase("relocating", "正在定位新会话…")
            new_session = self._wait_new_session(project, clear_at, exclude=session)
            warning = ""
            if new_session:
                pty.set_session(new_session)
                with self._pty_lock:
                    self._pty_sessions[role] = new_session
            else:
                warning = "未能定位新会话 JSONL，对话页可能仍显示旧会话"
            with self._handoff_lock:
                self._handoff.update(
                    {
                        "active": False,
                        "phase": "done",
                        "message": "交接完成，已进入新会话",
                        "session_after": new_session,
                        "warning": warning,
                        "finished_at": utc_now(),
                    }
                )
        except (OSError, RuntimeError, ValueError) as exc:
            with self._handoff_lock:
                self._handoff.update(
                    {
                        "active": False,
                        "phase": "error",
                        "message": f"交接失败：{exc}",
                        "error": str(exc),
                        "finished_at": utc_now(),
                    }
                )

    def _wait_handoff_reply(self, pty: PtyManager, project: str, session: str, offset: int) -> bool:
        deadline = time.monotonic() + handoff_timeout_seconds()
        current = offset
        while time.monotonic() < deadline:
            if not pty.status().get("running"):
                raise RuntimeError("PTY 已退出")
            if pty.session_id != session:
                return False
            result = read_appended(project, session, current)
            if result is not None:
                current = int(result.get("offset", current))
                for message in result.get("messages", []):
                    if message.get("role") == "assistant" and any(
                        block.get("kind") == "text" for block in message.get("blocks", [])
                    ):
                        return True
            time.sleep(0.5)
        return False

    def _wait_new_session(self, project: str, after: float, exclude: str) -> str:
        deadline = time.monotonic() + HANDOFF_RELOCATE_SECONDS
        while time.monotonic() < deadline:
            session = newest_session(project, after=after - 0.5, exclude=exclude)
            if session:
                return session
            time.sleep(0.5)
        return ""

    def pty_for(self, role: str, agent_id: str = "") -> PtyManager:
        """返回绑定该 Agent 的唯一实例；role 相同但 agent_id 变化视为换人。"""
        role = role.strip()
        agent_id = agent_id.strip()
        with self._pty_lock:
            if self.pty.role == role and self.pty.agent_id == agent_id:
                return self.pty
            if self.pty.role and self.pty.session_id:
                # 冲突重试可能换了 session id，切走前记住最新值。
                self._pty_sessions[self.pty.role] = self.pty.session_id
            self.pty.stop()
            session = self._pty_sessions.get(role)
            if not session:
                session = (
                    self._pty_session_override
                    if role == "" and self._pty_session_override
                    else str(uuid.uuid4())
                )
                self._pty_sessions[role] = session
            self.pty = self._new_pty(role, session, agent_id)
            return self.pty

    def stop_pty_for_role(self, role: str, agent_id: str = "") -> bool:
        role = role.strip()
        agent_id = agent_id.strip()
        with self._pty_lock:
            if self.pty.role != role:
                return False
            if agent_id and self.pty.agent_id != agent_id:
                # 旧句柄（同 role、旧 agent_id）不得误杀新 Agent 的 PTY。
                return False
            if self.pty.session_id:
                self._pty_sessions[self.pty.role] = self.pty.session_id
            if self.pty.status().get("running"):
                self.pty.stop()
            return True

    def check_agent_id(self, role: str, agent_id: str) -> bool:
        role = role.strip()
        agent_id = agent_id.strip()
        if not role or not agent_id:
            return False
        record = self.registry.get(role)
        return record is not None and _agent_id(record) == agent_id

    def role_online(self, role: str) -> bool:
        role = role.strip()
        if not role:
            return True
        record = self.registry.get(role)
        if record is None or record.status in ("stopped", "lost", "failed", "pending"):
            return False
        return process_alive(record.pid)

    def enforce_pty_binding(self) -> bool:
        """PTY 绑定的 Agent 离线/停止/被同角色新注册顶替时，立即回收 PTY 与 claude。"""
        role = self.pty.role
        if not role:
            return False
        record = self.registry.get(role)
        if record is None:
            return self.stop_pty_for_role(role)
        if record.status in ("stopped", "lost", "failed", "pending"):
            return self.stop_pty_for_role(role)
        if not process_alive(record.pid) or _agent_id(record) != self.pty.agent_id:
            return self.stop_pty_for_role(role)
        return False

    def _agent_dict(self, record: AgentRecord) -> dict[str, Any]:
        data = record.to_dict()
        data["alive"] = process_alive(record.pid)
        data["agent_id"] = _agent_id(record)
        data["external_session_id"] = self.external_session(record.role)
        return data

    def external_session(self, role: str) -> str | None:
        """读取该角色最新且仍存活的 MCP 缓存，返回其真实 Claude session id。"""
        cache_dir = self.home / AGENT_CACHE_DIR
        if not cache_dir.is_dir():
            return None
        best: tuple[str, str] | None = None
        for path in cache_dir.glob("*.json"):
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(parsed, dict):
                continue
            if _str(parsed.get("role")).strip().casefold() != role.casefold():
                continue
            project = _str(parsed.get("project")).strip()
            if project and _normalize_project(project) != self.project:
                continue
            mcp_pid = _int(parsed.get("mcp_pid"))
            if mcp_pid <= 0 or not process_alive(mcp_pid):
                continue
            session_id = _str(parsed.get("session_id")).strip()
            if re.fullmatch(r"[A-Za-z0-9-]{8,64}", session_id) is None:
                continue
            updated = _str(parsed.get("updated_at")).strip()
            if not updated:
                try:
                    updated = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
                except OSError:
                    updated = ""
            if best is None or updated > best[0]:
                best = (updated, session_id)
        return best[1] if best is not None else None

    @property
    def revision(self) -> int:
        with self._condition:
            return self._revision

    @property
    def is_stopping(self) -> bool:
        return self._stopping.is_set()

    def wait_for_change(self, last_seen: int) -> int:
        """空闲时无超时阻塞（零唤醒）；被 notify_all 唤醒后去抖并返回最新 revision。"""
        with self._condition:
            self._condition.wait()
            if self._revision == last_seen:
                return last_seen
            self._condition.wait(WATCH_DEBOUNCE_SECONDS)
            return self._revision

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._condition:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._condition:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def _publish(self, event: str = "snapshot") -> None:
        """写事务提交后调用：先取快照，再短暂持锁递增 revision 并通知。"""
        snapshot = self.status()
        with self._condition:
            self._revision += 1
            revision = self._revision
            subscribers = tuple(self._subscribers)
            self._condition.notify_all()
        frame = {"revision": revision, "event": event, "data": snapshot}
        for subscriber in subscribers:
            _offer_latest(subscriber, frame)

    def _publish_shutdown(self) -> None:
        with self._condition:
            self._revision += 1
            revision = self._revision
            subscribers = tuple(self._subscribers)
            self._condition.notify_all()
        frame = {"revision": revision, "event": "shutdown", "data": {"stopping": True}}
        for subscriber in subscribers:
            _offer_latest(subscriber, frame)

    def _wait_subscribers(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._condition:
                if not self._subscribers:
                    return
            time.sleep(0.02)

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        role = _role(payload)
        reported_project = _str(payload.get("project")).strip()
        if reported_project and _normalize_project(reported_project) != self.project:
            raise ProjectMismatch(
                f"project mismatch: daemon={self.project} agent={_normalize_project(reported_project)}"
            )
        now = utc_now()
        existing = self.registry.get(role)
        requested_started_at = _str(payload.get("started_at")).strip()
        started_at = requested_started_at or (existing.started_at if existing is not None else now)
        record = AgentRecord(
            role=role,
            pid=_int(payload.get("pid")),
            pgid=_int(payload.get("pgid")),
            job_id=_str(payload.get("job_id")),
            status=_str(payload.get("status")) or "registered",
            started_at=started_at,
            last_heartbeat=now,
            command=_str(payload.get("command")),
            updated_at=now,
            current_task_id=_str(payload.get("current_task_id")),
            cost_tokens=_int(payload.get("cost_tokens")),
            cost_budget=(
                _int(payload.get("cost_budget")) if payload.get("cost_budget") is not None else None
            ),
            project=self.project,
        )
        self.registry.upsert(record)
        self._publish()
        return self._agent_dict(record)

    def resolve(self, role: str) -> dict[str, Any] | None:
        record = self.registry.get(role)
        return self._agent_dict(record) if record is not None else None

    def _reconcile_agent_cache(self) -> tuple[bool, int, int]:
        """home/agents/<session>.json：存活会话恢复登记；死缓存保留给 --resume，按 TTL 清理。"""
        cache_dir = self.home / AGENT_CACHE_DIR
        if not cache_dir.is_dir():
            return False, 0, 0
        changed = False
        recovered = 0
        pruned = 0
        for path in sorted(cache_dir.glob("*.json")):
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                parsed = None
            data = dict(parsed) if isinstance(parsed, dict) else None
            role = _str(data.get("role")).strip() if data else ""
            project = _str(data.get("project")).strip() if data else ""
            if data is None or not role:
                pruned += int(_unlink(path))
                continue
            # 共享 home 时其它项目的缓存只跳过、不删除，避免跨项目互删。
            if project and _normalize_project(project) != self.project:
                continue
            if _cache_expired(data, path):
                pruned += int(_unlink(path))
                continue
            mcp_pid = _int(data.get("mcp_pid"))
            claude_pid = _int(data.get("claude_pid"))
            if (
                mcp_pid <= 0
                or not process_alive(mcp_pid)
                or (claude_pid > 0 and not process_alive(claude_pid))
            ):
                # 进程已死也保留角色缓存：--resume 复用同一 session id 时需要它。
                continue
            existing = self.registry.get(role)
            now = utc_now()
            started_at = _str(data.get("started_at")).strip() if data else ""
            if not started_at:
                started_at = existing.started_at if existing is not None else now
            session = _str(data.get("session")).strip() if data else ""
            ref = _str(data.get("ref")).strip() if data else ""
            suffix = f" session={session}" if session else ""
            suffix += f" ref={ref}" if ref else ""
            self.registry.upsert(
                AgentRecord(
                    role=role,
                    pid=mcp_pid,
                    pgid=mcp_pid,
                    job_id="",
                    status="registered",
                    started_at=started_at,
                    last_heartbeat=now,
                    command=f"mcp_server{suffix}".strip(),
                    updated_at=now,
                    project=self.project,
                )
            )
            recovered += 1
            changed = True
        return changed, recovered, pruned

    def reconcile_pending_agents(self) -> tuple[bool, int, int]:
        """home/pending/<session>.json：按 PID + create_time 惰性发现 Agent。"""
        pending_dir = self.home / PENDING_DIR
        if not pending_dir.is_dir():
            return False, 0, 0
        files = sorted(pending_dir.glob("*.json"))
        if not files:
            return False, 0, 0
        force_full = len(files) > PENDING_CLEANUP_THRESHOLD
        changed = False
        registered = 0
        pruned = 0
        for path in files:
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pruned += int(_unlink(path))
                continue
            data = parsed if isinstance(parsed, dict) else None
            role = _str(data.get("role")).casefold() if data else ""
            pid = _int(data.get("pid")) if data else 0
            cwd = _str(data.get("cwd")) if data else ""
            raw_create = data.get("pid_create_time") if data else None
            if (
                data is None
                or not role
                or _ROLE_PATTERN.fullmatch(role) is None
                or pid <= 0
                or not cwd
                or not isinstance(raw_create, (int, float))
            ):
                pruned += int(_unlink(path))
                continue
            if _normalize_project(cwd) != self.project:
                # 共享 home 时其它项目的 PID File 留给对应 daemon。
                continue
            try:
                actual_create = psutil.Process(pid).create_time()
            except (psutil.Error, OSError):
                pruned += int(_unlink(path))
                continue
            if abs(float(raw_create) - actual_create) > PID_CREATE_TIME_TOLERANCE:
                pruned += int(_unlink(path))
                continue
            existing = self.registry.get(role)
            if existing is not None and existing.pid == pid and existing.status != "stopped":
                continue
            now = utc_now()
            started_at = (
                existing.started_at
                if existing is not None and existing.pid == pid
                else datetime.fromtimestamp(actual_create, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            )
            self.registry.upsert(
                AgentRecord(
                    role=role,
                    pid=pid,
                    pgid=pid,
                    job_id="",
                    status="registered",
                    started_at=started_at,
                    last_heartbeat=now,
                    command=f"pidfile {path.stem}",
                    updated_at=now,
                    project=self.project,
                )
            )
            registered += 1
            changed = True
        if force_full:
            pruned += self._dedupe_pending(files)
        return changed, registered, pruned

    def _dedupe_pending(self, files: list[Path]) -> int:
        """超过阈值时全量清理：同一 role 只保留 mtime 最新的 PID File。"""
        newest: dict[str, tuple[float, Path]] = {}
        pruned = 0
        for path in files:
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
                data = parsed if isinstance(parsed, dict) else None
                mtime = path.stat().st_mtime
            except (OSError, json.JSONDecodeError):
                continue
            role = _str(data.get("role")).casefold() if data else ""
            if not role:
                continue
            current = newest.get(role)
            if current is None or mtime > current[0]:
                if current is not None:
                    pruned += int(_unlink(current[1]))
                newest[role] = (mtime, path)
            else:
                pruned += int(_unlink(path))
        return pruned

    def discover(self) -> dict[str, Any]:
        """手动检测：恢复存活会话缓存、清理死缓存，并重新探测已登记 agent 的存活。"""
        changed, recovered, pruned = self._reconcile_agent_cache()
        pending_changed, pending_registered, pending_pruned = self.reconcile_pending_agents()
        changed = pending_changed or changed
        recovered += pending_registered
        pruned += pending_pruned
        agents = self.registry.list()
        for record in agents:
            alive = process_alive(record.pid)
            if alive and record.status in ("stopped", "lost"):
                changed = self.registry.set_status(record.role, "registered") or changed
            elif not alive and record.status != "stopped":
                changed = self.registry.set_status(record.role, "stopped") or changed
                self.stop_pty_for_role(record.role)
        if changed:
            self._publish()
        agents = self.registry.list()
        excluded_pids = {os.getpid()}
        excluded_pids.update(record.pid for record in agents if record.status != "stopped")
        wild = [
            candidate.to_dict()
            for candidate in self.wild_driver.discover(self.project, excluded_pids)
        ]
        return {
            "discovered": True,
            "recovered": recovered,
            "pruned": pruned,
            "pending_registered": pending_registered,
            "pending_pruned": pending_pruned,
            "agents": [self._agent_dict(record) for record in agents],
            "wild": wild,
        }

    def cleanup_wild(
        self,
        payload: dict[str, Any],
        *,
        process_factory: Callable[[int], Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """强制清理未注册的 node/python 残留进程（需 create_time 防 PID 复用）。"""
        pid = _int(payload.get("pid"))
        if pid <= 0:
            return 400, {"error": "pid is required"}
        raw_create = payload.get("create_time")
        if isinstance(raw_create, bool) or not isinstance(raw_create, (int, float)):
            return 400, {"error": "create_time is required", "reason": "missing_create_time"}
        create_time = float(raw_create)
        if create_time <= 0:
            return 400, {"error": "create_time is required", "reason": "missing_create_time"}

        factory = process_factory or psutil.Process
        protected = {record.pid for record in self.registry.list() if record.pid > 0}
        protected.add(os.getpid())
        ancestor = os.getpid()
        seen: set[int] = set()
        while ancestor > 0 and ancestor not in seen:
            seen.add(ancestor)
            protected.add(ancestor)
            try:
                ancestor = int(factory(ancestor).ppid())
            except (psutil.Error, OSError):
                break
        if pid in protected:
            return 409, {"error": "process is protected", "reason": "protected_process"}

        try:
            process = factory(pid)
            name = str(process.name()).casefold()
        except psutil.NoSuchProcess:
            return 404, {"error": "process not found", "reason": "not_found"}
        except (psutil.Error, OSError) as exc:
            return 500, {"error": f"cannot inspect process: {exc}"}
        if name not in WILD_CLEANUP_ALLOWED_EXES:
            return 400, {"error": f"process not cleanable: {name}", "reason": "not_cleanable"}
        try:
            actual_create = float(process.create_time())
        except (psutil.Error, OSError) as exc:
            return 500, {"error": f"cannot inspect process: {exc}"}
        if abs(actual_create - create_time) > 0.05:
            return 409, {"error": "process identity changed", "reason": "stale_process"}
        try:
            cwd = str(process.cwd())
        except (psutil.Error, OSError):
            cwd = ""
        if not _within(cwd, _normalize_path(self.project)):
            return 403, {"error": "process is outside the project", "reason": "outside_project"}

        killed = kill_process_tree(pid, create_time, process_factory=factory)
        self.wild_driver.clear_cache()
        if not killed:
            return 409, {"error": "process already exited", "reason": "stale_process"}
        return 200, {"cleaned": True, "pid": pid, "killed": killed}

    def _sweep_leftover_workers(self) -> None:
        try:
            excluded = {record.pid for record in self.registry.list() if record.pid > 0}
            killed = sweep_leftover_real_workers(Path(self.project), excluded_pids=excluded)
        except (psutil.Error, OSError, RuntimeError) as exc:
            print(f"leftover worker sweep skipped: {exc}", file=sys.stderr, flush=True)
            return
        if killed:
            print(f"leftover claude -p workers reclaimed: {killed}", file=sys.stderr, flush=True)

    def status(self) -> dict[str, Any]:
        port = self.port
        if self.server is not None:
            port = int(self.server.server_address[1])
        return {
            "daemon": {
                "pid": os.getpid(),
                "host": self.host,
                "port": port,
                "started_at": self.started_at,
                "project": self.project,
            },
            "agents": [self._agent_dict(record) for record in self.registry.list()],
        }

    def start(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        role = _role(payload)
        if not bool(payload.get("dummy")):
            return 400, {"error": "P1 only supports --dummy workers"}
        existing = self.registry.get(role)
        if existing is not None and existing.status == "running" and process_alive(existing.pid):
            return 409, {
                "error": f"role already running: {role}",
                "agent": self._agent_dict(existing),
            }
        worker = self.launcher.spawn_dummy(role)
        now = utc_now()
        record = AgentRecord(
            role=role,
            pid=worker.pid,
            pgid=worker.pgid,
            job_id=worker.job_id,
            status="running",
            started_at=now,
            last_heartbeat=now,
            command=f"dummy_worker --role {role}",
            updated_at=now,
            project=self.project,
        )
        self.registry.upsert(record)
        self._publish()
        return 200, {"agent": self._agent_dict(record)}

    def stop(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        role = _role(payload)
        record = self.registry.get(role)
        if record is None:
            return 404, {"error": f"unknown role: {role}"}
        agent_id = _str(payload.get("agent_id"))
        if agent_id and _agent_id(record) != agent_id:
            # 旧前端句柄（同 role、旧 agent_id）不得误杀新注册的同名 Agent。
            return 409, {
                "error": f"stale agent id for {role}",
                "reason": "stale_agent",
                "agent": self._agent_dict(record),
            }
        if not record.job_id:
            # 外部会话不归中控杀，但必须释放绑定在它名下的 PTY（工作台幽灵终端）。
            self.stop_pty_for_role(role, agent_id)
            return 409, {
                "error": f"external agent cannot be stopped: {role}",
                "reason": "external_agent",
                "agent": self._agent_dict(record),
            }
        try:
            reclaim = self.launcher.stop(role)
        except KeyError:
            if process_alive(record.pid):
                self.stop_pty_for_role(role, agent_id)
                return 409, {
                    "error": f"agent is not managed by this daemon: {role}",
                    "reason": "external_agent",
                    "agent": self._agent_dict(record),
                }
            reclaim = {"role": role, "note": "worker already exited"}
        self.registry.set_status(role, "stopped")
        self.stop_pty_for_role(role, agent_id)
        self._publish()
        return 200, {"stopped": True, "role": role, "reclaim": reclaim}

    def need(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        result = self.router.need(payload)
        self._publish()
        return result

    def ack(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        result = self.router.ack(payload)
        self._publish()
        return result

    def completed(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        result = self.router.completed(payload)
        self._publish()
        return result

    def tasks(self, *, role: str = "", status: str = "") -> dict[str, Any]:
        return self.router.tasks(role=role, status=status)

    def task_detail(self, task_id: str) -> dict[str, Any]:
        return self.router.task_detail(task_id)

    def usage(self, *, task_id: str = "", role: str = "", day: str = "") -> dict[str, Any]:
        return self.router.usage(task_id=task_id, role=role, day=day)

    def observability(self) -> dict[str, Any]:
        data = self.router.observability()
        port = self.port
        if self.server is not None:
            port = int(self.server.server_address[1])
        data["daemon"] = {
            "pid": os.getpid(),
            "host": self.host,
            "port": port,
            "started_at": self.started_at,
        }
        return data

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        role = _role(payload)
        raw_task_id = payload.get("task_id")
        task_id = raw_task_id.strip() if isinstance(raw_task_id, str) else ""
        result = {"heartbeat": self.router.heartbeat(role, task_id)}
        self._publish("heartbeat")
        return result

    def step(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.router.upsert_step(payload)
        self._publish()
        return result

    def effect(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.router.add_effect(payload)
        self._publish()
        return result

    def steps(self, task_id: str) -> dict[str, Any]:
        return self.router.steps(task_id)

    def effects(self, task_id: str) -> dict[str, Any]:
        return self.router.effects(task_id)

    def effect_get(self, idem_key: str) -> dict[str, Any]:
        return self.router.effect(idem_key)

    def request_shutdown(self) -> None:
        self._stopping.set()
        server = self.server
        if server is not None:
            threading.Thread(target=server.shutdown, daemon=True).start()

    def _monitor_loop(self) -> None:
        interval = max(
            0.05,
            min(self.ack_timeout / 5.0, self.lost_seconds / 4.0, 1.0),
        )
        while not self._stop_monitor.wait(interval):
            try:
                changed = bool(self.router.check_timeouts(self.ack_timeout))
                lost = self.router.check_heartbeats(self.lost_seconds)
                for action in lost:
                    self.stop_pty_for_role(str(action.get("role", "")))
                changed = bool(lost) or changed
                changed = bool(self.router.check_task_budgets()) or changed
                changed = self.enforce_pty_binding() or changed
                now = time.monotonic()
                if now - self._last_watchdog >= self.watchdog_seconds:
                    self._last_watchdog = now
                    changed = bool(self.router.watch_workers()) or changed
                if now - self._last_agent_cache >= agent_cache_seconds():
                    self._last_agent_cache = now
                    changed = self._reconcile_agent_cache()[0] or changed
                    changed = self.reconcile_pending_agents()[0] or changed
                if changed:
                    self._publish()
            except Exception:  # noqa: BLE001, S112 - 监控线程不因单次异常退出
                continue

    def serve(
        self,
        *,
        dashboard: bool = True,
        watch: bool = False,
        show_heartbeat: bool = False,
        sweep_workers: bool = False,
    ) -> None:
        if not _acquire_instance_lock(self.home):
            raise OSError(f"orchestrator already running for {self.home}")
        try:
            if _port_in_use(self.host, self.port):
                raise OSError(f"orchestrator port {self.port} already in use")
            if sweep_workers:
                self._sweep_leftover_workers()
            self._serve_forever(
                dashboard=dashboard,
                watch=watch,
                show_heartbeat=show_heartbeat,
            )
        finally:
            _release_instance_lock(self.home)

    def _serve_forever(
        self,
        *,
        dashboard: bool,
        watch: bool,
        show_heartbeat: bool,
    ) -> None:
        server = _Server((self.host, self.port), _Handler)
        server.daemon = self
        self.server = server
        port = int(server.server_address[1])
        self.router.orchestrator_url = f"http://{self.host}:{port}"
        write_daemon_info(
            self.home,
            {
                "pid": os.getpid(),
                "host": self.host,
                "port": port,
                "started_at": self.started_at,
            },
        )
        self._stop_monitor.clear()
        self._stop_dashboard.clear()
        monitor = threading.Thread(target=self._monitor_loop, daemon=True)
        monitor.start()
        dashboard_thread: threading.Thread | None = None
        watch_thread: threading.Thread | None = None
        if dashboard:
            print(render_dashboard(self, port), flush=True)
            if watch:
                watch_thread = threading.Thread(
                    target=_watch_loop,
                    args=(self, self._stop_dashboard, show_heartbeat),
                    daemon=True,
                )
                watch_thread.start()
            else:
                refresh = dashboard_refresh_seconds()
                if refresh > 0:
                    dashboard_thread = threading.Thread(
                        target=_dashboard_loop,
                        args=(self, port, refresh, self._stop_dashboard),
                        daemon=True,
                    )
                    dashboard_thread.start()
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            self._stop_monitor.set()
            self._stop_dashboard.set()
            self._publish_shutdown()
            monitor.join(timeout=5)
            if dashboard_thread is not None:
                dashboard_thread.join(timeout=5)
            if watch_thread is not None:
                watch_thread.join(timeout=5)
            self._wait_subscribers(timeout=2.0)
            self.chat.stop_all()
            self.pty.stop()
            self.launcher.stop_all()
            server.server_close()
            (self.home / "daemon.json").unlink(missing_ok=True)


class _Server(ThreadingHTTPServer):
    daemon: Daemon

    def handle_error(self, request: Any, client_address: Any) -> None:
        # SSE / keep-alive 客户端主动断开是正常情况，不打印 traceback。
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    server_version = "SentinelOrchestrator/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def daemon(self) -> Daemon:
        server = self.server
        if not isinstance(server, _Server):
            raise TypeError("unexpected server type")
        return server.daemon

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _authorized(self, path: str) -> bool:
        if path == "/health":
            return True
        header = self.headers.get("Authorization", "")
        return hmac.compare_digest(header, f"Bearer {self.daemon.token}")

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            parsed = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(400, {"error": "invalid JSON"})
            return None
        if not isinstance(parsed, dict):
            self._send(400, {"error": "JSON object required"})
            return None
        return dict(parsed)

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError):
            # 客户端已断开（常见于前端取消/刷新），不是 daemon 错误。
            return

    def _agent_guard(self, role: str, agent_id: str) -> dict[str, Any] | None:
        """角色请求必须携带注册期唯一 agent_id；空 role 保留旧版全局行为。"""
        if not role:
            return None
        if not agent_id:
            return {"error": "agent_id is required", "reason": "agent_id_required"}
        if not self.daemon.check_agent_id(role, agent_id):
            return {"error": f"stale agent id for {role}", "reason": "stale_agent"}
        if not self.daemon.role_online(role):
            return {"error": f"agent offline: {role}", "reason": "agent_offline"}
        return None

    def _pty_preflight_error(self) -> dict[str, Any] | None:
        check = self.daemon.pty_preflight()
        return None if check.get("ok") else check

    def _workspaces_payload(self) -> dict[str, Any]:
        active = self.daemon.active_workspace()
        return {
            "workspaces": self.daemon.workspaces.list(),
            "active_id": active.get("id"),
            "active_preflight": self.daemon.pty_preflight(),
            "engines": [
                {
                    "id": adapter.id,
                    "label": adapter.label,
                    "default_command": adapter.default_command,
                    **adapter.capabilities(),
                }
                for adapter in ENGINES.values()
            ],
        }

    def _pty_role_guard(self, role: str, agent_id: str) -> dict[str, Any] | None:
        """输入/缩放必须命中当前绑定的角色与 agent_id；空 role 保留旧版全局行为。"""
        if not role:
            return None
        error = self._agent_guard(role, agent_id)
        if error is not None:
            return error
        bound = self.daemon.pty
        if bound.role != role or bound.agent_id != agent_id:
            return {
                "error": f"pty bound to {bound.role or 'global'}/{bound.agent_id or '-'}",
                "reason": "role_mismatch",
                "bound_role": bound.role,
                "bound_agent_id": bound.agent_id,
            }
        return None

    def _stream_pty_chat(self, offset: int, role: str = "", agent_id: str = "") -> None:
        """JSONL 旁路：只读轮询 PTY 会话 transcript，增量推送问答。"""
        pty = self.daemon.pty_for(role, agent_id)
        try:
            pty.ensure_started()
        except (OSError, RuntimeError) as exc:
            self._send(500, {"error": f"pty start failed: {exc}"})
            return
        session = pty.session_id
        current = offset
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
        except (ConnectionResetError, BrokenPipeError):
            return
        last_ping = time.monotonic()
        notice_seq = 0
        while True:
            notice = pty.latest_notice()
            if notice and int(notice.get("seq", 0)) > notice_seq:
                notice_seq = int(notice["seq"])
                self._write_bytes(
                    f"event: notice\ndata: {json.dumps(notice, ensure_ascii=False)}\n\n".encode()
                )
            if pty.session_id != session:
                self._write_bytes(
                    f"event: session\ndata: {json.dumps({'session_id': pty.session_id})}\n\n".encode()
                )
                return
            result = read_appended(self.daemon.transcript_project(), session, current)
            if result is not None:
                if result["reset"]:
                    current = 0
                    self._write_bytes(b"event: reset\ndata: {}\n\n")
                else:
                    if result["context_tokens"]:
                        self._write_bytes(
                            f"event: context\ndata: {json.dumps({'context_tokens': result['context_tokens']})}\n\n".encode()
                        )
                    usage = result.get("usage") or {}
                    if any(usage.values()):
                        self._write_bytes(
                            f"event: usage\ndata: {json.dumps({'usage': usage}, ensure_ascii=False)}\n\n".encode()
                        )
                    for message in result["messages"]:
                        frame = json.dumps({"message": message}, ensure_ascii=False)
                        if not self._write_bytes(f"event: message\ndata: {frame}\n\n".encode()):
                            return
                    current = result["offset"]
            now = time.monotonic()
            if now - last_ping >= pty.ping_seconds():
                if not self._write_bytes(b": ping\n\n"):
                    return
                last_ping = now
            if not pty.status().get("running"):
                self._write_bytes(b"event: exit\ndata: {}\n\n")
                return
            time.sleep(0.6)

    def _stream_pty(self, role: str = "", agent_id: str = "") -> None:
        pty = self.daemon.pty_for(role, agent_id)
        try:
            pty.ensure_started()
        except (OSError, RuntimeError) as exc:
            self._send(500, {"error": f"pty start failed: {exc}"})
            return
        subscriber, backlog = pty.subscribe()
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
        except (ConnectionResetError, BrokenPipeError):
            pty.unsubscribe(subscriber)
            return
        try:
            for seq, chunk in backlog:
                frame = {"seq": seq, "data": base64.b64encode(chunk).decode("ascii")}
                if not self._write_bytes(f"event: output\ndata: {json.dumps(frame)}\n\n".encode()):
                    return
            notice = pty.latest_notice()
            if notice:
                self._write_bytes(
                    f"event: notice\ndata: {json.dumps(notice, ensure_ascii=False)}\n\n".encode()
                )
            if not pty.status().get("running"):
                self._write_bytes(
                    f"event: exit\ndata: {json.dumps({'exit_status': None})}\n\n".encode()
                )
                return
            while True:
                try:
                    event, data = subscriber.get(timeout=pty.ping_seconds())
                except queue.Empty:
                    if not self._write_bytes(b": ping\n\n"):
                        return
                    continue
                if event == "output":
                    frame = {
                        "seq": data.get("seq"),
                        "data": base64.b64encode(data.get("data", b"")).decode("ascii"),
                    }
                    if not self._write_bytes(
                        f"event: output\ndata: {json.dumps(frame)}\n\n".encode()
                    ):
                        return
                elif event == "notice":
                    self._write_bytes(
                        f"event: notice\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()
                    )
                elif event == "exit":
                    self._write_bytes(f"event: exit\ndata: {json.dumps(data)}\n\n".encode())
                    return
        finally:
            pty.unsubscribe(subscriber)

    def _stream_chat(self, payload: dict[str, Any]) -> None:
        try:
            role = _role(payload)
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            self._send(400, {"error": "prompt is required"})
            return
        if len(prompt) > PROMPT_MAX_CHARS:
            self._send(400, {"error": f"prompt too long (>{PROMPT_MAX_CHARS})"})
            return
        if self.daemon.chat.is_busy(role):
            self._send(409, {"error": "role busy", "reason": "chat_busy", "role": role})
            return
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
        except (ConnectionResetError, BrokenPipeError):
            return

        def emit(event: str, data: str) -> bool:
            return self._write_bytes(f"event: {event}\ndata: {data}\n\n".encode())

        try:
            summary = self.daemon.chat.stream_turn(role, prompt.strip(), emit)
        except ChatBusy:
            summary = {"role": role, "error": "chat_busy", "reason": "chat_busy"}
        emit("end", json.dumps(summary, ensure_ascii=False))

    def _send_demo_page(self, token: str) -> None:
        body = _DEMO_PAGE.replace("__TOKEN__", json.dumps(token)).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError):
            return

    def _write_bytes(self, data: bytes) -> bool:
        """写 SSE 字节；客户端断开时返回 False，由调用方结束流。"""
        try:
            self.wfile.write(data)
            self.wfile.flush()
        except (ConnectionResetError, BrokenPipeError):
            return False
        return True

    def _write_event(self, revision: int, event: str, data: dict[str, Any]) -> bool:
        body = json.dumps(data, ensure_ascii=False)
        frame = f"id: {revision}\nevent: {event}\ndata: {body}\n\n"
        return self._write_bytes(frame.encode("utf-8"))

    def _stream_events(self) -> None:
        daemon = self.daemon
        subscriber = daemon.subscribe()
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            sent = daemon.revision
            if not self._write_event(sent, "snapshot", daemon.status()):
                return
            ping = sse_ping_seconds()
            while True:
                try:
                    frame = subscriber.get(timeout=ping)
                except queue.Empty:
                    if daemon.is_stopping:
                        break
                    if not self._write_bytes(b": ping\n\n"):
                        return
                    continue
                revision = int(frame["revision"])
                if revision <= sent:  # 并发发布可能乱序到达，丢弃旧帧
                    continue
                if not self._write_event(revision, str(frame["event"]), frame["data"]):
                    return
                sent = revision
                if frame["event"] == "shutdown":
                    break
        except OSError:
            return
        finally:
            daemon.unsubscribe(subscriber)

    def _write_log_frame(self, seq: int, role: str, line: str, event: str = "log") -> bool:
        payload = (
            {"role": role, "line": line, "ts": utc_now()} if event == "log" else {"stopping": True}
        )
        frame = f"id: {seq}\nevent: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        return self._write_bytes(frame.encode("utf-8"))

    def _report_log(self, payload: dict[str, Any]) -> None:
        """外部会话上报（POST /logs/report）：追加到 logs/<role>.log，供 /logs/stream 跟随。"""
        role = str(payload.get("role", "")).strip()
        line = " ".join(str(payload.get("line", "")).split())[:REPORT_LINE_MAX]
        if role != "daemon" and not _ROLE_PATTERN.match(role):
            self._send(400, {"error": "invalid role"})
            return
        if not line:
            self._send(400, {"error": "line required"})
            return
        logs_dir = self.daemon.home / "logs"
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            with (logs_dir / f"{role}.log").open("a", encoding="utf-8") as stream:
                stream.write(f"{line}\n")
        except OSError as exc:
            self._send(500, {"error": f"write failed: {exc}"})
            return
        self._send(200, {"reported": True, "role": role, "chars": len(line)})

    def _log_targets(self) -> list[tuple[str, Path]] | None:
        """解析 ?role= 或列出 logs/*.log；非法 role 返回 None（已发送 400）。"""
        logs_dir = self.daemon.home / "logs"
        raw = parse_qs(urlparse(self.path).query).get("role", [""])[0].strip()
        if raw:
            if raw != "daemon" and not _ROLE_PATTERN.match(raw):
                self._send(400, {"error": "invalid role"})
                return None
            return [(raw, logs_dir / f"{raw}.log")]
        if not logs_dir.is_dir():
            return []
        targets: list[tuple[str, Path]] = []
        for path in sorted(logs_dir.glob("*.log")):
            role = path.stem
            if role == "daemon" or _ROLE_PATTERN.match(role):
                targets.append((role, path))
        return targets

    def _stream_logs(self) -> None:
        """SSE 日志流：先回放每个文件最后 tail 行，再按文件增长持续推送。"""
        targets = self._log_targets()
        if targets is None:
            return
        query = parse_qs(urlparse(self.path).query)
        try:
            tail = int(query.get("tail", [str(LOG_TAIL_DEFAULT)])[0])
        except ValueError:
            tail = LOG_TAIL_DEFAULT
        tail = max(0, min(tail, LOG_TAIL_MAX))
        specific_role = bool(query.get("role", [""])[0].strip())
        logs_dir = self.daemon.home / "logs"
        daemon = self.daemon
        self.close_connection = True
        seq = 0
        offsets: dict[Path, int] = {}
        pending: dict[Path, bytes] = {}
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            for role, path in targets:
                for line in _tail_lines(path, tail):
                    seq += 1
                    if not self._write_log_frame(seq, role, line):
                        return
                try:
                    offsets[path] = path.stat().st_size
                except OSError:
                    offsets[path] = 0
            ping = sse_ping_seconds()
            last_emit = time.monotonic()
            rescan_at = last_emit + 2.0
            while True:
                if daemon.is_stopping:
                    seq += 1
                    self._write_log_frame(seq, "daemon", "", event="shutdown")
                    break
                emitted = False
                for role, path in targets:
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    offset = offsets.get(path, 0)
                    if size < offset:  # 文件被截断/轮转
                        offset = 0
                        pending[path] = b""
                    if size <= offset:
                        continue
                    try:
                        with path.open("rb") as stream:
                            stream.seek(offset)
                            chunk = stream.read(size - offset)
                    except OSError:
                        continue
                    offsets[path] = offset + len(chunk)
                    lines, pending[path] = _split_log_chunk(pending.get(path, b""), chunk)
                    for line in lines:
                        seq += 1
                        if not self._write_log_frame(seq, role, line):
                            return
                        emitted = True
                now = time.monotonic()
                if not specific_role and now >= rescan_at:
                    rescan_at = now + 2.0
                    known = {path for _, path in targets}
                    for path in sorted(logs_dir.glob("*.log")):
                        role = path.stem
                        if path in known or not (role == "daemon" or _ROLE_PATTERN.match(role)):
                            continue
                        targets.append((role, path))
                        offsets[path] = 0
                if emitted:
                    last_emit = now
                elif now - last_emit >= ping:
                    if not self._write_bytes(b": ping\n\n"):
                        return
                    last_emit = now
                time.sleep(LOG_POLL_SECONDS)
        except OSError:
            return

    def _dispatch(self, method: str) -> None:
        path = urlparse(self.path).path
        if method == "GET" and path == "/chat/demo":
            query = parse_qs(urlparse(self.path).query)
            supplied = (query.get("token") or [""])[0]
            if not hmac.compare_digest(supplied, self.daemon.token):
                self._send(401, {"error": "unauthorized"})
                return
            self._send_demo_page(supplied)
            return
        if not self._authorized(path):
            self._send(401, {"error": "unauthorized"})
            return
        if method == "GET" and path == "/sessions/history":
            query = parse_qs(urlparse(self.path).query)
            raw_role = (query.get("role") or [""])[0]
            try:
                role = _role({"role": raw_role})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
                return
            try:
                limit = int((query.get("limit") or ["200"])[0])
            except ValueError:
                limit = 200
            session = self.daemon.external_session(role)
            if not session:
                self._send(404, {"error": "no terminal session", "role": role})
                return
            history = read_history(self.daemon.transcript_project(), session, limit=limit)
            if history is None:
                self._send(404, {"error": "transcript not found", "role": role})
                return
            self._send(
                200,
                {
                    "role": role,
                    "session_id": session,
                    "source": "terminal",
                    **history,
                },
            )
            return

        if method == "GET" and path == "/pty/history":
            query = parse_qs(urlparse(self.path).query)
            role = _query_role(query)
            agent_id = _query_agent_id(query)
            error = self._agent_guard(role, agent_id)
            if error is not None:
                self._send(409 if error.get("reason") != "agent_id_required" else 400, error)
                return
            preflight_error = self._pty_preflight_error()
            if preflight_error is not None:
                self._send(400, preflight_error)
                return
            pty = self.daemon.pty_for(role, agent_id)
            try:
                pty.ensure_started()
            except (OSError, RuntimeError) as exc:
                self._send(500, {"error": f"pty start failed: {exc}"})
                return
            session = pty.session_id
            history = read_history(self.daemon.transcript_project(), session)
            if history is None:
                history = {
                    "messages": [],
                    "truncated": False,
                    "context_tokens": 0,
                    "usage": dict.fromkeys(USAGE_KEYS, 0),
                    "bytes": 0,
                    "offset": 0,
                }
            self._send(
                200,
                {
                    "session_id": session,
                    "source": "pty",
                    "agent_id": agent_id,
                    "budget_tokens": self.daemon.workspace_budget(),
                    **history,
                },
            )
            return

        if method == "GET" and path == "/pty/chat/stream":
            query = parse_qs(urlparse(self.path).query)
            role = _query_role(query)
            agent_id = _query_agent_id(query)
            try:
                offset = max(0, int((query.get("offset") or ["0"])[0]))
            except ValueError:
                offset = 0
            error = self._agent_guard(role, agent_id)
            if error is not None:
                self._send(409 if error.get("reason") != "agent_id_required" else 400, error)
                return
            preflight_error = self._pty_preflight_error()
            if preflight_error is not None:
                self._send(400, preflight_error)
                return
            self._stream_pty_chat(offset, role, agent_id)
            return

        if method == "GET" and path == "/pty/status":
            query = parse_qs(urlparse(self.path).query)
            role = _query_role(query)
            agent_id = _query_agent_id(query)
            pty_status = self.daemon.pty.status()
            if role:
                pty_status["role_online"] = self.daemon.role_online(role)
                pty_status["agent_match"] = self.daemon.check_agent_id(role, agent_id)
            self._send(200, pty_status)
            return

        if method == "GET" and path == "/pty/stream":
            query = parse_qs(urlparse(self.path).query)
            role = _query_role(query)
            agent_id = _query_agent_id(query)
            error = self._agent_guard(role, agent_id)
            if error is not None:
                self._send(409 if error.get("reason") != "agent_id_required" else 400, error)
                return
            preflight_error = self._pty_preflight_error()
            if preflight_error is not None:
                self._send(400, preflight_error)
                return
            self._stream_pty(role, agent_id)
            return

        if method == "GET" and path == "/handoff":
            self._send(200, self.daemon.handoff_status())
            return

        if method == "GET" and path == "/workspaces":
            self._send(200, self._workspaces_payload())
            return

        if method == "GET" and path == "/events":
            self._stream_events()
            return
        if method == "GET" and path == "/logs/stream":
            self._stream_logs()
            return
        payload: dict[str, Any] = {}
        if method == "POST":
            parsed = self._read_json()
            if parsed is None:
                return
            payload = parsed

        if method == "POST" and path == "/logs/report":
            self._report_log(payload)
            return

        if method == "POST" and path == "/discover":
            self._send(200, self.daemon.discover())
            return

        if method == "POST" and path == "/wild/cleanup":
            try:
                status, body = self.daemon.cleanup_wild(payload)
            except (OSError, RuntimeError) as exc:
                status, body = 500, {"error": str(exc)}
            self._send(status, body)
            return

        if method == "POST" and path == "/handoff":
            role = _optional_role(payload)
            agent_id = _optional_agent_id(payload)
            error = self._pty_role_guard(role, agent_id)
            if error is not None:
                self._send(409 if error.get("reason") != "agent_id_required" else 400, error)
                return
            status, body = self.daemon.start_handoff(role, agent_id)
            self._send(status, body)
            return

        if method == "POST" and path == "/workspaces/preflight":
            self._send(
                200,
                preflight(_str(payload.get("command")), _str(payload.get("project_path"))),
            )
            return

        if method == "POST" and path == "/workspaces/save":
            try:
                workspace = self.daemon.workspaces.save(
                    payload, activate=bool(payload.get("activate"))
                )
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
                return
            if workspace.get("active"):
                self.daemon.reset_pty()
            check = preflight(
                str(workspace.get("command", "")), str(workspace.get("project_path", ""))
            )
            workspace_data = self._workspaces_payload()
            workspace_data["workspace"] = {**workspace, "preflight": check}
            self._send(200, workspace_data)
            return

        if method == "POST" and path == "/workspaces/delete":
            if not self.daemon.workspaces.delete(_int(payload.get("id"))):
                self._send(404, {"error": "workspace not found"})
                return
            self.daemon.reset_pty()
            self._send(200, self._workspaces_payload())
            return

        if method == "POST" and path == "/workspaces/activate":
            try:
                workspace = self.daemon.activate_workspace(_int(payload.get("id")))
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
                return
            check = preflight(
                str(workspace.get("command", "")), str(workspace.get("project_path", ""))
            )
            workspace_data = self._workspaces_payload()
            workspace_data["workspace"] = {**workspace, "preflight": check}
            self._send(200, workspace_data)
            return

        if method == "POST" and path == "/pty/start":
            role = _optional_role(payload)
            agent_id = _optional_agent_id(payload)
            error = self._agent_guard(role, agent_id)
            if error is not None:
                self._send(409 if error.get("reason") != "agent_id_required" else 400, error)
                return
            preflight_error = self._pty_preflight_error()
            if preflight_error is not None:
                self._send(400, preflight_error)
                return
            try:
                self._send(200, self.daemon.pty_for(role, agent_id).start())
            except (OSError, RuntimeError) as exc:
                self._send(500, {"error": str(exc)})
            return

        if method == "POST" and path == "/pty/input":
            data = payload.get("data")
            if not isinstance(data, str):
                self._send(400, {"error": "data is required"})
                return
            role = _optional_role(payload)
            agent_id = _optional_agent_id(payload)
            error = self._pty_role_guard(role, agent_id)
            if error is not None:
                self._send(409 if error.get("reason") != "agent_id_required" else 400, error)
                return
            try:
                ok = self.daemon.pty.write(data)
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
                return
            if ok:
                self._send(200, {"written": len(data)})
            else:
                self._send(409, {"error": "pty not running", "reason": "pty_not_running"})
            return

        if method == "POST" and path == "/pty/resize":
            try:
                cols = int(payload.get("cols", 120))
                rows = int(payload.get("rows", 30))
            except (TypeError, ValueError):
                self._send(400, {"error": "invalid cols/rows"})
                return
            role = _optional_role(payload)
            agent_id = _optional_agent_id(payload)
            error = self._pty_role_guard(role, agent_id)
            if error is not None:
                self._send(409 if error.get("reason") != "agent_id_required" else 400, error)
                return
            self._send(200, self.daemon.pty.resize(cols, rows))
            return

        if method == "POST" and path == "/pty/stop":
            role = _optional_role(payload)
            agent_id = _optional_agent_id(payload)
            if role and not agent_id:
                self._send(400, {"error": "agent_id is required", "reason": "agent_id_required"})
                return
            if role:
                stopped = self.daemon.stop_pty_for_role(role, agent_id)
                self._send(200, {"stopped": stopped, "role": role, "agent_id": agent_id})
                return
            self._send(200, self.daemon.pty.stop())
            return

        if method == "POST" and path in ("/chat", "/chat/stop"):
            self._send(
                410,
                {
                    "error": "headless chat disabled; use PTY",
                    "reason": "pty_only",
                },
            )
            return

        if method == "GET" and path == "/health":
            self._send(
                200,
                {"ok": True, "pid": os.getpid(), "started_at": self.daemon.started_at},
            )
        elif method == "GET" and path == "/status":
            self._send(200, self.daemon.status())
        elif method == "POST" and path == "/register":
            try:
                self._send(200, {"agent": self.daemon.register(payload)})
            except ProjectMismatch as exc:
                self._send(409, {"error": str(exc)})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
        elif method == "POST" and path == "/resolve":
            try:
                role = _role(payload)
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
                return
            agent = self.daemon.resolve(role)
            if agent is None:
                self._send(404, {"error": f"unknown role: {role}"})
            else:
                self._send(200, {"agent": agent})
        elif method == "POST" and path == "/start":
            try:
                status, body = self.daemon.start(payload)
            except ValueError as exc:
                status, body = 400, {"error": str(exc)}
            except (OSError, RuntimeError, TimeoutError) as exc:
                status, body = 500, {"error": str(exc)}
            self._send(status, body)
        elif method == "POST" and path == "/stop":
            try:
                status, body = self.daemon.stop(payload)
            except ValueError as exc:
                status, body = 400, {"error": str(exc)}
            except (OSError, RuntimeError) as exc:
                status, body = 500, {"error": str(exc)}
            self._send(status, body)
        elif method == "POST" and path == "/need":
            try:
                status, body = self.daemon.need(payload)
            except RoleBusy as exc:
                status, body = 409, {"error": str(exc)}
            except RoleBudgetExceeded as exc:
                status, body = 429, {"error": str(exc)}
            except ValueError as exc:
                status, body = 400, {"error": str(exc)}
            except (OSError, RuntimeError, TimeoutError) as exc:
                status, body = 500, {"error": str(exc)}
            self._send(status, body)
        elif method == "POST" and path == "/ack":
            try:
                status, body = self.daemon.ack(payload)
            except TaskNotFound as exc:
                status, body = 404, {"error": str(exc)}
            except RoleMismatch as exc:
                status, body = 400, {"error": str(exc)}
            except InvalidTransition as exc:
                status, body = 409, {"error": str(exc)}
            except ValueError as exc:
                status, body = 400, {"error": str(exc)}
            self._send(status, body)
        elif method == "POST" and path == "/completed":
            try:
                status, body = self.daemon.completed(payload)
            except TaskNotFound as exc:
                status, body = 404, {"error": str(exc)}
            except RoleMismatch as exc:
                status, body = 400, {"error": str(exc)}
            except InvalidTransition as exc:
                status, body = 409, {"error": str(exc)}
            except ValueError as exc:
                status, body = 400, {"error": str(exc)}
            self._send(status, body)
        elif method == "GET" and path == "/tasks":
            query = parse_qs(urlparse(self.path).query)
            self._send(
                200,
                self.daemon.tasks(
                    role=query.get("role", [""])[0],
                    status=query.get("status", [""])[0],
                ),
            )
        elif method == "POST" and path == "/task":
            task_id = payload.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                self._send(400, {"error": "task_id is required"})
            else:
                try:
                    self._send(200, self.daemon.task_detail(task_id.strip()))
                except TaskNotFound as exc:
                    self._send(404, {"error": str(exc)})
        elif method == "POST" and path == "/heartbeat":
            try:
                self._send(200, self.daemon.heartbeat(payload))
            except TaskNotFound as exc:
                self._send(404, {"error": str(exc)})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
        elif method == "POST" and path == "/step":
            try:
                self._send(200, self.daemon.step(payload))
            except TaskNotFound as exc:
                self._send(404, {"error": str(exc)})
            except InvalidTransition as exc:
                self._send(409, {"error": str(exc)})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
        elif method == "POST" and path == "/effect":
            try:
                self._send(200, self.daemon.effect(payload))
            except TaskNotFound as exc:
                self._send(404, {"error": str(exc)})
            except InvalidTransition as exc:
                self._send(409, {"error": str(exc)})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
        elif method == "GET" and path == "/steps":
            query = parse_qs(urlparse(self.path).query)
            task_id = query.get("task_id", [""])[0]
            if not task_id:
                self._send(400, {"error": "task_id is required"})
            else:
                try:
                    self._send(200, self.daemon.steps(task_id))
                except TaskNotFound as exc:
                    self._send(404, {"error": str(exc)})
        elif method == "GET" and path == "/effect":
            query = parse_qs(urlparse(self.path).query)
            idem_key = query.get("idem_key", [""])[0]
            task_id = query.get("task_id", [""])[0]
            if idem_key:
                try:
                    self._send(200, self.daemon.effect_get(idem_key))
                except TaskNotFound as exc:
                    self._send(404, {"error": str(exc)})
            elif task_id:
                try:
                    self._send(200, self.daemon.effects(task_id))
                except TaskNotFound as exc:
                    self._send(404, {"error": str(exc)})
            else:
                self._send(400, {"error": "idem_key or task_id is required"})
        elif method == "GET" and path == "/usage":
            query = parse_qs(urlparse(self.path).query)
            self._send(
                200,
                self.daemon.usage(
                    task_id=query.get("task", [""])[0],
                    role=query.get("role", [""])[0],
                    day=query.get("day", [""])[0],
                ),
            )
        elif method == "GET" and path == "/observability":
            self._send(200, self.daemon.observability())
        elif method == "POST" and path == "/shutdown":
            self._send(200, {"stopping": True, "pid": os.getpid()})
            self.daemon.request_shutdown()
        else:
            self._send(404, {"error": "not found"})


def _install_signal_handlers(daemon: Daemon) -> None:
    def handler(signum: int, frame: FrameType | None) -> None:
        daemon.request_shutdown()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        value = getattr(signal, name, None)
        if value is not None:
            signal.signal(value, handler)


def main(argv: list[str] | None = None) -> int:
    _configure_stdio(sys.stdout, sys.stderr)
    parser = argparse.ArgumentParser(prog="orchestrator-service")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--home", default="")
    parser.add_argument("--no-dashboard", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--show-heartbeat", action="store_true")
    args = parser.parse_args(argv)
    home = Path(args.home).resolve() if args.home else default_home()
    try:
        daemon = Daemon(home, args.host, args.port)
        _install_signal_handlers(daemon)
        daemon.serve(
            dashboard=not args.no_dashboard,
            watch=args.watch,
            show_heartbeat=args.show_heartbeat,
            sweep_workers=_should_sweep_workers(),
        )
    except OSError as exc:
        print(f"orchestrator failed to start: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
