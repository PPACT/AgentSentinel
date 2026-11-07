"""P1 最小 daemon：``127.0.0.1`` + token，常驻持有 Job 句柄与 registry。

CLI 只是 HTTP 客户端；daemon 退出（正常或崩溃）时 Job 句柄关闭，
``KILL_ON_JOB_CLOSE`` 会回收全部 worker 与子孙进程。
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import urlparse

from tools.orchestrator.launcher import Launcher, process_alive
from tools.orchestrator.registry import AgentRecord, Registry, utc_now

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOST = "127.0.0.1"
_ROLE_PATTERN = re.compile(r"^[a-z0-9_-]+$")


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
        self.host = host
        self.port = port
        self.token = load_or_create_token(home)
        self.registry = Registry(home / "registry.sqlite3")
        self.launcher = Launcher(home)
        self.server: ThreadingHTTPServer | None = None
        self.started_at = utc_now()
        self._stopping = threading.Event()

    def _agent_dict(self, record: AgentRecord) -> dict[str, Any]:
        data = record.to_dict()
        data["alive"] = process_alive(record.pid)
        return data

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        role = _role(payload)
        now = utc_now()
        record = AgentRecord(
            role=role,
            pid=_int(payload.get("pid")),
            pgid=_int(payload.get("pgid")),
            job_id=_str(payload.get("job_id")),
            status=_str(payload.get("status")) or "registered",
            started_at=_str(payload.get("started_at")) or now,
            last_heartbeat=now,
            command=_str(payload.get("command")),
            updated_at=now,
        )
        self.registry.upsert(record)
        return self._agent_dict(record)

    def resolve(self, role: str) -> dict[str, Any] | None:
        record = self.registry.get(role)
        return self._agent_dict(record) if record is not None else None

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
        )
        self.registry.upsert(record)
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
        return 200, {"stopped": True, "role": role, "reclaim": reclaim}

    def request_shutdown(self) -> None:
        self._stopping.set()
        server = self.server
        if server is not None:
            threading.Thread(target=server.shutdown, daemon=True).start()

    def serve(self) -> None:
        server = _Server((self.host, self.port), _Handler)
        server.daemon = self
        self.server = server
        port = int(server.server_address[1])
        write_daemon_info(
            self.home,
            {
                "pid": os.getpid(),
                "host": self.host,
                "port": port,
                "started_at": self.started_at,
            },
        )
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            self.launcher.stop_all()
            server.server_close()
            (self.home / "daemon.json").unlink(missing_ok=True)


class _Server(ThreadingHTTPServer):
    daemon: Daemon


class _Handler(BaseHTTPRequestHandler):
    server_version = "SentinelOrchestrator/0.1"

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
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method: str) -> None:
        path = urlparse(self.path).path
        if not self._authorized(path):
            self._send(401, {"error": "unauthorized"})
            return
        payload: dict[str, Any] = {}
        if method == "POST":
            parsed = self._read_json()
            if parsed is None:
                return
            payload = parsed

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
    parser = argparse.ArgumentParser(prog="orchestrator-service")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--home", default="")
    args = parser.parse_args(argv)
    home = Path(args.home).resolve() if args.home else default_home()
    daemon = Daemon(home, args.host, args.port)
    _install_signal_handlers(daemon)
    daemon.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
