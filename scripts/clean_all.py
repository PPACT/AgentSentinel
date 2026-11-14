"""兜底清理：只结束本项目 Workspace / daemon / PTY / 前端相关进程。

安全边界：
- 只认本项目路径（项目根、%APPDATA%\\AgentSentinel、各 workspace home）或本次启动的
  daemon/PTY pid；绝不按 python.exe / node.exe 名字全杀，也不按端口盲杀。
- 系统与终端进程白名单；自身及所有祖先进程永不结束。
- 清理前把 PID / 名称 / 命中原因 / 命令行 / cwd 写入项目根 clean_all.log。
- `--dry-run` 只扫描并写日志，不结束任何进程。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import psutil  # type: ignore[import-untyped]  # psutil 未随包提供 stubs

ROOT = Path(__file__).resolve().parents[1]
ROOT_TEXT = str(ROOT).lower()
FRONTEND_DIR = ROOT / "experiments" / "frontend-dashboard-tauri"
FRONTEND_TEXT = str(FRONTEND_DIR).lower()
HOME = ROOT / ".claude" / "orchestrator"
LEGACY_WORKSPACES_DB = ROOT / ".claude" / "workspaces.sqlite3"
LEGACY_WORKSPACES_ROOT = ROOT / ".claude" / "workspaces"
LOG_PATH = ROOT / "clean_all.log"
VITE_PORT = 1420

WHITELIST_NAMES = {
    "system",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "winlogon.exe",
    "svchost.exe",
    "explorer.exe",
    "dwm.exe",
    "fontdrvhost.exe",
    "taskhostw.exe",
    "sihost.exe",
    "ctfmon.exe",
    "runtimebroker.exe",
    "searchhost.exe",
    "shellexperiencehost.exe",
    "startmenuexperiencehost.exe",
    "textinputhost.exe",
    "applicationframehost.exe",
    "conhost.exe",
    "openconsole.exe",
    "windowsterminal.exe",
}


def user_data_dir() -> Path:
    override = os.environ.get("SENTINEL_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "AgentSentinel"
    return Path.home() / ".agentsentinel"


DATA_DIR = user_data_dir()
APP_DATA_TEXT = str(DATA_DIR).lower()
WORKSPACES_DB = DATA_DIR / "workspaces.sqlite3"
WORKSPACES_ROOT = DATA_DIR / "workspaces"

targets: dict[int, str] = {}
ports: dict[int, int] = {}
self_pid = os.getpid()
excluded_pids: set[int] = {self_pid}
try:
    excluded_pids.update(parent.pid for parent in psutil.Process(self_pid).parents())
except (psutil.Error, OSError):
    excluded_pids.add(psutil.Process(self_pid).ppid())


def belongs_to_project(text: str) -> bool:
    lowered = text.lower()
    return ROOT_TEXT in lowered or APP_DATA_TEXT in lowered


def add(pid: object, reason: str) -> None:
    if isinstance(pid, bool) or not isinstance(pid, int):
        return
    if pid <= 0 or pid in excluded_pids or pid in targets:
        return
    targets[pid] = reason


def read_state_at(home: Path, name: str) -> dict[str, object]:
    try:
        data = json.loads((home / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def workspace_homes() -> list[Path]:
    homes: set[Path] = {HOME}
    for db_path, workspace_root in (
        (WORKSPACES_DB, WORKSPACES_ROOT),
        (LEGACY_WORKSPACES_DB, LEGACY_WORKSPACES_ROOT),
    ):
        if not db_path.exists():
            continue
        try:
            with sqlite3.connect(db_path) as connection:
                for workspace_id, daemon_home in connection.execute(
                    "SELECT id, daemon_home FROM workspaces"
                ):
                    if daemon_home:
                        homes.add(Path(str(daemon_home)))
                    elif int(workspace_id) == 1:
                        homes.add(HOME)
                    else:
                        homes.add(workspace_root / str(workspace_id))
        except (sqlite3.Error, OSError):
            pass
    for workspace_root in (WORKSPACES_ROOT, LEGACY_WORKSPACES_ROOT):
        if workspace_root.is_dir():
            for child in workspace_root.iterdir():
                if child.is_dir():
                    homes.add(child)
    return sorted(homes)


def process_text(pid: int) -> tuple[str, str, str, str]:
    """返回 (name, cmdline, cwd, exe)；失败字段用空串。"""
    try:
        process = psutil.Process(pid)
        name = process.name()
    except (psutil.Error, OSError):
        return "", "", "", ""
    try:
        cmdline = " ".join(process.cmdline())
    except (psutil.Error, OSError):
        cmdline = ""
    try:
        cwd = process.cwd()
    except (psutil.Error, OSError):
        cwd = ""
    try:
        exe = process.exe()
    except (psutil.Error, OSError):
        exe = ""
    return name, cmdline, cwd, exe


def collect() -> None:
    sessions: set[str] = set()
    for home in workspace_homes():
        daemon = read_state_at(home, "daemon.json")
        pty = read_state_at(home, "pty.json")
        daemon_pid = daemon.get("pid")
        add(daemon_pid if isinstance(daemon_pid, int) else 0, f"daemon:{home.name}")
        if isinstance(daemon_pid, int):
            raw_port = daemon.get("port")
            if isinstance(raw_port, int) and not isinstance(raw_port, bool):
                ports[daemon_pid] = raw_port
        pty_pid = pty.get("pid")
        add(pty_pid if isinstance(pty_pid, int) else 0, f"pty:{home.name}")
        session = str(pty.get("session_id") or "").strip().lower()
        if session:
            sessions.add(session)

    # 只对 1420 做端口辅助识别，且必须是本项目 frontend 目录的 node/vite。
    try:
        connections = psutil.net_connections(kind="tcp")
    except (psutil.Error, OSError):
        connections = []
    for connection in connections:
        if (
            connection.status != psutil.CONN_LISTEN
            or connection.laddr.port != VITE_PORT
            or not connection.pid
        ):
            continue
        name, cmdline, _cwd, _exe = process_text(connection.pid)
        if name.lower() == "node.exe" and FRONTEND_TEXT in cmdline.lower():
            add(connection.pid, f"vite:{VITE_PORT}")
            ports[connection.pid] = VITE_PORT

    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            pid = int(process.info["pid"])
            name = (process.info.get("name") or "").lower()
            cmdline = " ".join(process.info.get("cmdline") or [])
        except (psutil.Error, OSError, TypeError, ValueError):
            continue
        if name in WHITELIST_NAMES or pid in excluded_pids:
            continue
        lowered = cmdline.lower()
        _name, _cmd, cwd, exe = process_text(pid)

        is_daemon = (
            name.startswith("python")
            and (
                "-m tools.orchestrator.service" in lowered
                or "-m tools/orchestrator/service" in lowered
                or ("dummy_worker" in lowered and ROOT_TEXT in lowered)
            )
            and "mcp_server" not in lowered
            and belongs_to_project(cmdline)
        )
        is_frontend = (
            name == "node.exe"
            and ("vite.js" in lowered or "tauri.mjs" in lowered or "@tauri-apps/cli" in lowered)
            and (FRONTEND_TEXT in lowered or cwd.lower().startswith(FRONTEND_TEXT))
        )
        is_tauri = name in {"cargo-tauri.exe", "tauri.exe"} and (
            FRONTEND_TEXT in lowered or ROOT_TEXT in lowered
        )
        is_npm = (
            name == "cmd.exe"
            and "tauri dev" in lowered
            and (cwd.lower().startswith(FRONTEND_TEXT) or cwd.lower().startswith(ROOT_TEXT))
        )
        is_app = name == "agentsentinel-console.exe" and exe.lower().startswith(ROOT_TEXT)
        is_pty_claude = name == "claude.exe" and (
            any(session in lowered for session in sessions)
            or "active orchestration task" in lowered
        )
        if is_daemon or is_frontend or is_tauri or is_npm or is_app or is_pty_claude:
            reason = (
                "daemon"
                if is_daemon
                else "vite"
                if is_frontend
                else "tauri"
                if is_tauri
                else "npm"
                if is_npm
                else "app"
                if is_app
                else "pty-claude"
            )
            add(pid, reason)

    # 只展开已确认属主进程的子孙；白名单与祖先继续排除。
    for pid in list(targets):
        try:
            children = psutil.Process(pid).children(recursive=True)
        except (psutil.Error, OSError):
            continue
        for child in children:
            if child.pid in excluded_pids:
                continue
            try:
                child_name = child.name().lower()
            except (psutil.Error, OSError):
                continue
            if child_name in WHITELIST_NAMES:
                continue
            add(child.pid, "descendant")


def write_log(dry_run: bool) -> None:
    lines = [
        f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} clean_all =====",
        f"root={ROOT}",
        f"mode={'dry-run' if dry_run else 'clean'} targets={len(targets)}",
        "PID | NAME | REASON | CMDLINE | CWD",
    ]
    for pid, reason in sorted(targets.items()):
        name, cmdline, cwd, _exe = process_text(pid)
        lines.append(f"{pid} | {name or '-'} | {reason} | {cmdline or '-'} | {cwd or '-'}")
    try:
        with LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n\n")
    except OSError:
        pass


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
    _gone, alive = psutil.wait_procs(processes, timeout=1.0)
    for process in alive:
        try:
            process.kill()
        except (psutil.Error, OSError):
            continue
    _gone, alive = psutil.wait_procs(alive, timeout=1.0)
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


def cleanup_stale_locks(dry_run: bool) -> list[str]:
    """删除无活进程持有的 pending/*.lock。

    只做非阻塞探测：能拿到锁说明持有进程已退出，文件才可删；拿不到锁说明有活
    Agent 持有，一律跳过。不尝试强制解锁活进程的锁（Windows 上不可能安全做到）。
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from tools.orchestrator.locking import probe, unlink_with_retry
    except ImportError:
        # 清理失败不能阻断进程清理主流程。
        return []

    removed: list[str] = []
    for home in workspace_homes():
        pending = home / "pending"
        if not pending.is_dir():
            continue
        for path in sorted(pending.glob("*.lock")):
            if probe(path) != "free":
                continue
            if dry_run or unlink_with_retry(path):
                removed.append(str(path))
    return removed


def released_ports(alive: list[int]) -> set[int]:
    killed = set(targets) - set(alive)
    return {port for pid, port in ports.items() if pid in killed}


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    collect()
    write_log(dry_run)
    if targets:
        print(f"[扫描] 发现 {len(targets)} 个候选进程。", flush=True)
    else:
        print("[扫描] 未发现残留进程。", flush=True)
    print(f"[详情] 命中清单：{LOG_PATH}", flush=True)
    if dry_run:
        stale_locks = cleanup_stale_locks(True)
        if stale_locks:
            print(f"[锁文件] dry-run 可清理 {len(stale_locks)} 个残留 .lock", flush=True)
        return 0

    alive = kill_all()
    stale_locks = cleanup_stale_locks(False)
    remove_stale_state(alive)
    if stale_locks:
        print(f"[锁文件] 已清理 {len(stale_locks)} 个残留 .lock", flush=True)
    killed = len(set(targets) - set(alive))
    port_count = len(released_ports(alive))
    if alive:
        print(
            f"[警告] 已清理 {killed} 个进程，仍有 {len(alive)} 个未退出："
            + ",".join(str(pid) for pid in alive)
        )
        return 1
    with LOG_PATH.open("a", encoding="utf-8") as stream:
        stream.write(
            f"RESULT killed={killed} ports={port_count} locks={len(stale_locks)} at "
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )
    print(f"[完成] 共清理了 {killed} 个进程，释放了 {port_count} 个端口。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
