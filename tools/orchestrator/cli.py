"""P1 CLI：register / resolve / status / start / stop 与 daemon 子命令。

CLI 只是 HTTP 客户端；daemon 常驻持有 Job 句柄，因此 CLI 退出不会回收 worker。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, request

from tools.orchestrator.launcher import process_alive
from tools.orchestrator.service import default_home

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIMEOUT = 30.0
START_TIMEOUT = 15.0


def _read_daemon_info(home: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((home / "daemon.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _token(home: Path) -> str:
    try:
        return (home / "token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"error": "invalid daemon response"}
    return data if isinstance(data, dict) else {"value": data}


def _request(
    home: Path,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, dict[str, Any]]:
    info = _read_daemon_info(home)
    if info is None:
        return 0, {"error": "daemon not running"}
    host = str(info.get("host", "127.0.0.1"))
    port = int(info.get("port", 0))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    headers = {"Authorization": f"Bearer {_token(home)}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(f"http://{host}:{port}{path}", data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return int(response.status), _decode(response.read())
    except error.HTTPError as exc:
        return int(exc.code), _decode(exc.read())
    except (OSError, error.URLError) as exc:
        return 0, {"error": f"daemon request failed: {exc}"}


def _healthy(home: Path) -> bool:
    status, body = _request(home, "GET", "/health", timeout=2.0)
    return status == 200 and bool(body.get("ok"))


def _daemon_start(home: Path, port: int) -> dict[str, Any]:
    info = _read_daemon_info(home)
    if info is not None and _healthy(home):
        return {"running": True, "already_running": True, **info}
    home.mkdir(parents=True, exist_ok=True)
    log_path = home / "logs" / "daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(home)
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    with log_path.open("a", encoding="utf-8") as stream:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.orchestrator.service",
                "--serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--home",
                str(home),
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            close_fds=True,
        )
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        info = _read_daemon_info(home)
        if info is not None and _healthy(home):
            return {"running": True, "already_running": False, **info}
        time.sleep(0.1)
    raise RuntimeError("daemon did not become healthy")


def _daemon_stop(home: Path) -> dict[str, Any]:
    info = _read_daemon_info(home)
    if info is None:
        return {"running": False, "note": "daemon.json missing"}
    status, body = _request(home, "POST", "/shutdown")
    pid = int(info.get("pid", 0))
    deadline = time.monotonic() + START_TIMEOUT
    while pid > 0 and process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    alive = process_alive(pid) if pid > 0 else False
    if not alive:
        (home / "daemon.json").unlink(missing_ok=True)
    return {
        "running": bool(alive),
        "shutdown_status": status,
        "response": body,
    }


def _ensure_daemon(home: Path) -> None:
    if _healthy(home):
        return
    _daemon_start(home, 0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orchestrator")
    parser.add_argument("--home", default="", help="orchestrator home directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    daemon = subparsers.add_parser("daemon", help="manage the orchestrator daemon")
    daemon_sub = daemon.add_subparsers(dest="action", required=True)
    daemon_start = daemon_sub.add_parser("start")
    daemon_start.add_argument("--port", type=int, default=0)
    daemon_sub.add_parser("stop")
    daemon_sub.add_parser("status")

    register = subparsers.add_parser("register", help="register an external agent")
    register.add_argument("--role", required=True)
    register.add_argument("--pid", type=int, default=0)
    register.add_argument("--status", default="registered")
    register.add_argument("--command", dest="command_text", default="")

    resolve = subparsers.add_parser("resolve", help="resolve a role")
    resolve.add_argument("--role", required=True)

    subparsers.add_parser("status", help="daemon and agent status")

    start = subparsers.add_parser("start", help="start a dummy worker")
    start.add_argument("--role", required=True)
    start.add_argument("--dummy", action="store_true")

    stop = subparsers.add_parser("stop", help="stop a worker")
    stop.add_argument("--role", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    home = Path(args.home).resolve() if args.home else default_home()
    try:
        if args.command == "daemon":
            if args.action == "start":
                result = _daemon_start(home, int(args.port))
            elif args.action == "stop":
                result = _daemon_stop(home)
            else:
                result = {"running": _healthy(home), "info": _read_daemon_info(home)}
            print(json.dumps(result, ensure_ascii=False))
            return 0

        _ensure_daemon(home)
        status = 0
        body: dict[str, Any] = {}
        if args.command == "register":
            status, body = _request(
                home,
                "POST",
                "/register",
                {
                    "role": args.role,
                    "pid": int(args.pid),
                    "status": args.status,
                    "command": args.command_text,
                },
            )
        elif args.command == "resolve":
            status, body = _request(home, "POST", "/resolve", {"role": args.role})
        elif args.command == "status":
            status, body = _request(home, "GET", "/status")
        elif args.command == "start":
            status, body = _request(
                home, "POST", "/start", {"role": args.role, "dummy": bool(args.dummy)}
            )
        elif args.command == "stop":
            status, body = _request(home, "POST", "/stop", {"role": args.role})
        else:
            raise ValueError(f"unknown command: {args.command}")
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps(body, ensure_ascii=False))
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
