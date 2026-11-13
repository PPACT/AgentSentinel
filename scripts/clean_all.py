"""兜底清理：结束本项目 daemon、PTY(claude)、前端与占用端口的残留进程。

由 `clean_all.bat` 调用。只匹配本项目相关进程，不会动用户其他 Python/Claude
会话；`--dry-run` 只打印候选，不执行结束操作。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import psutil  # type: ignore[import-untyped]  # psutil 未随包提供 stubs

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / ".claude" / "orchestrator"
WORKSPACES_DB = ROOT / ".claude" / "workspaces.sqlite3"
DEFAULT_PORTS = (1420, 8765, 9222)

targets: dict[int, str] = {}
self_pid = os.getpid()
parent_pid = psutil.Process(self_pid).ppid()


def add(pid: object, reason: str) -> None:
    if isinstance(pid, bool) or not isinstance(pid, (int, str)):
        return
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return
    if value <= 0 or value in (self_pid, parent_pid):
        return
    targets.setdefault(value, reason)


def read_state_at(home: Path, name: str) -> dict[str, object]:
    try:
        data = json.loads((home / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def workspace_homes() -> list[Path]:
    """控制面 + 所有已配置/已落盘的 Workspace daemon home。"""
    homes: set[Path] = {HOME}
    if WORKSPACES_DB.exists():
        try:
            with sqlite3.connect(WORKSPACES_DB) as connection:
                for workspace_id, daemon_home in connection.execute(
                    "SELECT id, daemon_home FROM workspaces"
                ):
                    if daemon_home:
                        homes.add(Path(str(daemon_home)))
                    elif int(workspace_id) == 1:
                        homes.add(HOME)
                    else:
                        homes.add(ROOT / ".claude" / "workspaces" / str(workspace_id))
        except (sqlite3.Error, OSError):
            pass
    workspace_root = ROOT / ".claude" / "workspaces"
    if workspace_root.is_dir():
        for child in workspace_root.iterdir():
            if child.is_dir():
                homes.add(child)
    return sorted(homes)


def collect() -> None:
    ports = set(DEFAULT_PORTS)
    sessions: set[str] = set()
    for home in workspace_homes():
        daemon = read_state_at(home, "daemon.json")
        pty = read_state_at(home, "pty.json")
        add(daemon.get("pid"), f"daemon:{home.name}")
        add(pty.get("pid"), f"pty:{home.name}")
        raw_port = daemon.get("port")
        if isinstance(raw_port, (int, str)) and not isinstance(raw_port, bool):
            try:
                ports.add(int(raw_port))
            except ValueError:
                pass
        session = str(pty.get("session_id") or "").strip().lower()
        if session:
            sessions.add(session)

    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError):
        connections = []
    for connection in connections:
        if (
            connection.status == psutil.CONN_LISTEN
            and connection.laddr.port in ports
            and connection.pid
        ):
            add(connection.pid, f"port:{connection.laddr.port}")

    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (process.info.get("name") or "").lower()
            command = " ".join(process.info.get("cmdline") or []).lower()
        except (psutil.Error, OSError):
            continue
        is_daemon = (
            name.startswith("python")
            and (
                "tools.orchestrator.service" in command
                or "tools.orchestrator.dummy_worker" in command
                or "tools/orchestrator/service" in command
                or "tools/orchestrator/dummy_worker" in command
            )
            and "mcp_server" not in command
        )
        is_frontend = name == "node.exe" and ("vite.js" in command or "tauri-cli" in command)
        is_tauri = name in {"cargo-tauri.exe", "tauri.exe"}
        is_npm = name == "cmd.exe" and ("tauri dev" in command or "vite" in command)
        is_app = name == "agentsentinel-console.exe"
        is_pty_claude = name == "claude.exe" and (
            any(session in command for session in sessions)
            or "active orchestration task" in command
        )
        if is_daemon or is_frontend or is_tauri or is_npm or is_app or is_pty_claude:
            reason = "claude" if is_pty_claude else "process"
            add(process.info.get("pid"), reason)

    # 补全子孙进程（先收集再结束，避免漏掉 PTY 下的 claude）。
    for pid in list(targets):
        try:
            for child in psutil.Process(pid).children(recursive=True):
                add(child.pid, "child")
        except (psutil.Error, OSError):
            continue


def kill_all() -> list[int]:
    processes: list[psutil.Process] = []
    for pid in targets:
        try:
            processes.append(psutil.Process(pid))
        except (psutil.Error, OSError):
            continue
    for process in processes:
        try:
            process.terminate()
        except (psutil.Error, OSError):
            continue
    _gone, alive = psutil.wait_procs(processes, timeout=5)
    for process in alive:
        try:
            process.kill()
        except (psutil.Error, OSError):
            continue
    _gone, alive = psutil.wait_procs(alive, timeout=5)
    return [process.pid for process in alive]


def remove_stale_state(alive: list[int]) -> None:
    if alive:
        return
    for home in workspace_homes():
        for name in ("pty.json", "daemon.json"):
            try:
                (home / name).unlink()
            except OSError:
                pass


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    collect()
    if dry_run:
        if not targets:
            print("clean_all(dry-run): nothing matched.")
        for pid, reason in sorted(targets.items()):
            print(f"{pid}  {reason}")
        return 0

    alive = kill_all()
    remove_stale_state(alive)
    print(f"clean_all: targets={len(targets)} killed={len(targets) - len(alive)}")
    if alive:
        print("clean_all: still alive: " + ",".join(str(pid) for pid in alive))
        return 1
    print("clean_all: no related processes remain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
