"""P1 最小 daemon：``127.0.0.1`` + token，常驻持有 Job 句柄与 registry。

CLI 只是 HTTP 客户端；daemon 退出（正常或崩溃）时 Job 句柄关闭，
``KILL_ON_JOB_CLOSE`` 会回收全部 worker 与子孙进程。
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import queue
import re
import secrets
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import parse_qs, urlparse

from tools.orchestrator.launcher import Launcher, process_alive
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


def _role(payload: dict[str, Any]) -> str:
    value = payload.get("role")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("role is required")
    role = value.strip().casefold()
    if _ROLE_PATTERN.fullmatch(role) is None:
        raise ValueError(f"invalid role: {role}")
    return role


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
        self.ack_timeout = ack_timeout_seconds()
        self.lost_seconds = lost_timeout_seconds()
        self.watchdog_seconds = watchdog_seconds()
        self._last_watchdog = 0.0
        self.server: ThreadingHTTPServer | None = None
        self.started_at = utc_now()
        self._stopping = threading.Event()
        self._stop_monitor = threading.Event()
        self._stop_dashboard = threading.Event()
        self._revision = 0
        self._condition = threading.Condition()
        self._subscribers: list[queue.Queue[dict[str, Any]]] = []
        self.router.startup_recovery()

    def _agent_dict(self, record: AgentRecord) -> dict[str, Any]:
        data = record.to_dict()
        data["alive"] = process_alive(record.pid)
        return data

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

    def discover(self) -> dict[str, Any]:
        """手动检测：重新探测已登记 agent 的进程存活，修正 status 后返回最新列表。"""
        changed = False
        for record in self.registry.list():
            alive = process_alive(record.pid)
            if alive and record.status in ("stopped", "lost"):
                changed = self.registry.set_status(record.role, "registered") or changed
            elif not alive and record.status != "stopped":
                changed = self.registry.set_status(record.role, "stopped") or changed
        if changed:
            self._publish()
        return {
            "discovered": True,
            "agents": [self._agent_dict(record) for record in self.registry.list()],
        }

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
        try:
            reclaim = self.launcher.stop(role)
        except KeyError:
            reclaim = {"role": role, "note": "no live worker in launcher"}
        self.registry.set_status(role, "stopped")
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
                changed = bool(self.router.check_heartbeats(self.lost_seconds)) or changed
                changed = bool(self.router.check_task_budgets()) or changed
                now = time.monotonic()
                if now - self._last_watchdog >= self.watchdog_seconds:
                    self._last_watchdog = now
                    changed = bool(self.router.watch_workers()) or changed
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
        if not self._authorized(path):
            self._send(401, {"error": "unauthorized"})
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
        )
    except OSError as exc:
        print(f"orchestrator failed to start: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
