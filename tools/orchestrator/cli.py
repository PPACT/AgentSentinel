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
from typing import Any, cast
from urllib import error, request
from urllib.parse import urlencode

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
    # daemon 的 stdout/stderr 重定向到 UTF-8 日志文件；固定子进程编码，避免 cp936 乱码。
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
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
                "--no-dashboard",
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


def _render_observability(data: dict[str, Any]) -> str:
    lines: list[str] = []
    daemon = cast(dict[str, Any], data.get("daemon") or {})
    lines.append(f"daemon: pid={daemon.get('pid')} port={daemon.get('port')}")
    lines.append("agents:")
    agents = cast(list[Any], data.get("agents") or [])
    for agent in agents:
        if isinstance(agent, dict):
            lines.append(
                f"  {agent.get('role')}: status={agent.get('status')} "
                f"pid={agent.get('pid')} alive={agent.get('alive')} "
                f"task={agent.get('current_task_id', '')}"
            )
    tasks = cast(list[Any], data.get("tasks") or [])
    counts: dict[str, int] = {}
    for task in tasks:
        if isinstance(task, dict):
            key = str(task.get("status", "unknown"))
            counts[key] = counts.get(key, 0) + 1
    lines.append(
        "tasks: " + (", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none")
    )
    usage = cast(dict[str, Any], data.get("usage") or {})
    totals = cast(dict[str, Any], usage.get("totals") or {})
    lines.append(
        f"usage: runs={totals.get('runs', 0)} billed={totals.get('billed_tokens', 0)} "
        f"cost=${float(totals.get('cost_usd', 0.0)):.4f}"
    )
    roles = cast(list[Any], usage.get("roles") or [])
    for role in roles:
        if isinstance(role, dict):
            lines.append(
                f"  {role.get('role')}: billed={role.get('billed_tokens', 0)} "
                f"cost=${float(role.get('cost_usd', 0.0)):.4f}"
            )
    residual = cast(list[Any], data.get("residual") or [])
    lines.append(
        "residual: " + ("none" if not residual else json.dumps(residual, ensure_ascii=False))
    )
    recent = cast(list[Any], data.get("recent_messages") or [])
    if recent:
        lines.append("recent messages:")
        for message in recent[:5]:
            if isinstance(message, dict):
                lines.append(
                    f"  {message.get('created_at')} {message.get('type')} {message.get('task_id')}"
                )
    budgets = cast(dict[str, Any], data.get("budgets") or {})
    lines.append(
        f"budgets: role={budgets.get('role_budget_tokens')} "
        f"task_tokens={budgets.get('task_max_tokens')} "
        f"task_minutes={budgets.get('task_max_minutes')}"
    )
    return "\n".join(lines)


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

    status_parser = subparsers.add_parser("status", help="daemon and agent status")
    status_parser.add_argument("--verbose", action="store_true")

    usage_parser = subparsers.add_parser("usage", help="usage ledger and summary")
    usage_parser.add_argument("--task", default="")
    usage_parser.add_argument("--role", default="")
    usage_parser.add_argument("--day", default="")

    observability = subparsers.add_parser(
        "observability", help="agents/tasks/messages/residual/budget view"
    )
    observability.add_argument("--format", choices=["text", "json"], default="text")

    start = subparsers.add_parser("start", help="start a dummy worker")
    start.add_argument("--role", required=True)
    start.add_argument("--dummy", action="store_true")

    stop = subparsers.add_parser("stop", help="stop a worker")
    stop.add_argument("--role", required=True)

    need = subparsers.add_parser("need", help="create a task for a role")
    need.add_argument("--from-role", dest="from_role", required=True)
    need.add_argument("--to-role", dest="to_role", required=True)
    need.add_argument("--intent", required=True)
    need.add_argument("--pointer", required=True)
    need.add_argument("--acceptance", action="append", default=[])
    need.add_argument("--idempotency-key", dest="idempotency_key", default="")
    need.add_argument("--task-id", dest="task_id", default="")
    need.add_argument("--max-tokens", dest="max_tokens", type=int, default=0)
    need.add_argument("--max-minutes", dest="max_minutes", type=int, default=0)
    need.add_argument(
        "--managed",
        action="store_true",
        help="target 未注册时允许拉起托管 worker（需 SENTINEL_ORCH_ALLOW_MANAGED_WORKERS=1）",
    )

    ack = subparsers.add_parser("ack", help="acknowledge a delivered task")
    ack.add_argument("--task-id", dest="task_id", required=True)
    ack.add_argument("--role", required=True)

    completed = subparsers.add_parser("completed", help="mark a task completed")
    completed.add_argument("--task-id", dest="task_id", required=True)
    completed.add_argument("--role", required=True)
    completed.add_argument("--artifact", dest="artifact", action="append", default=[])
    completed.add_argument("--cost-tokens", dest="cost_tokens", type=int, default=0)

    tasks = subparsers.add_parser("tasks", help="list tasks")
    tasks.add_argument("--role", default="")
    tasks.add_argument("--status", default="")

    task = subparsers.add_parser("task", help="task detail with messages")
    task.add_argument("--task-id", dest="task_id", required=True)
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
            if args.verbose:
                status, body = _request(home, "GET", "/observability")
                if 200 <= status < 300:
                    print(_render_observability(body))
                    return 0
                print(json.dumps(body, ensure_ascii=False), file=sys.stderr)
                return 1
            status, body = _request(home, "GET", "/status")
        elif args.command == "usage":
            query = urlencode(
                {
                    key: value
                    for key, value in (
                        ("task", args.task),
                        ("role", args.role),
                        ("day", args.day),
                    )
                    if value
                }
            )
            status, body = _request(home, "GET", "/usage" + (f"?{query}" if query else ""))
        elif args.command == "observability":
            status, body = _request(home, "GET", "/observability")
            if 200 <= status < 300:
                if args.format == "text":
                    print(_render_observability(body))
                else:
                    print(json.dumps(body, ensure_ascii=False))
                return 0
            print(json.dumps(body, ensure_ascii=False), file=sys.stderr)
            return 1
        elif args.command == "start":
            status, body = _request(
                home, "POST", "/start", {"role": args.role, "dummy": bool(args.dummy)}
            )
        elif args.command == "stop":
            status, body = _request(home, "POST", "/stop", {"role": args.role})
        elif args.command == "need":
            need_payload: dict[str, Any] = {
                "from_role": args.from_role,
                "to_role": args.to_role,
                "intent": args.intent,
                "pointer": args.pointer,
                "acceptance": list(args.acceptance),
                "idempotency_key": args.idempotency_key,
                "task_id": args.task_id,
            }
            if args.max_tokens or args.max_minutes:
                need_payload["budget"] = {
                    "max_tokens": int(args.max_tokens),
                    "max_minutes": int(args.max_minutes),
                }
            if args.managed:
                need_payload["execution"] = "managed"
            status, body = _request(home, "POST", "/need", need_payload)
        elif args.command == "ack":
            status, body = _request(
                home, "POST", "/ack", {"task_id": args.task_id, "role": args.role}
            )
        elif args.command == "completed":
            status, body = _request(
                home,
                "POST",
                "/completed",
                {
                    "task_id": args.task_id,
                    "role": args.role,
                    "artifacts": list(args.artifact),
                    "cost_tokens": int(args.cost_tokens),
                },
            )
        elif args.command == "tasks":
            query = urlencode(
                {
                    key: value
                    for key, value in (("role", args.role), ("status", args.status))
                    if value
                }
            )
            status, body = _request(home, "GET", "/tasks" + (f"?{query}" if query else ""))
        elif args.command == "task":
            status, body = _request(home, "POST", "/task", {"task_id": args.task_id})
        else:
            raise ValueError(f"unknown command: {args.command}")
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps(body, ensure_ascii=False))
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
