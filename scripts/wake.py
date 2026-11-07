"""跨会话唤醒 v2：registry 查表 + 本机命名管道投递（仅标准库）。

设计要点：

* **Agent 只写文件**：``PostToolUse`` Hook（``wake_on_exchange.py``）在
  ``docs/local/交流区/交流区.md`` 被写入后调用 :func:`notify`。
* **registry 单文件**：``.claude/mailbox/registry.json``，只含 ``roles`` /
  ``sessions``；存活判断只看 pid，没有心跳、没有 stale 自愈。
* **投递通道**：读取目标会话在 ``~/.claude/sessions/<pid>.json`` 中登记的
  ``messagingSocketPath``，再从 ``<pid>.<sha256>.key`` 取 ``peerToken``，
  通过本机命名管道发送 ``auth`` + ``user`` 两行 JSON。该通道是 Claude Code
  本机 peer messaging 使用的 IPC（debug 输出也给出同款注入配方）。
* **降级**：目标无记录或 pid 不存活时，调用 ``scripts/dev_start.ps1 -Role``
  只拉目标角色窗口，并把脚本回报的 pid 写回 registry。

子命令只保留：

    notify --file <path> [--session-id <id>]
    register --role <role> --session <name> --ref <ref> --pid <pid>
    resolve --role <role>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TARGET_FILE = "docs/local/交流区/交流区.md"
PEER_BY_ROLE = {"doc": "dev", "dev": "doc"}
WAKE_TEXT_TEMPLATE = (
    "[唤醒 from={role}] 交流区已更新（" + TARGET_FILE + "）。"
    "请读取该文件与 STATUS.md 头部后继续；回执只发指针。"
)
SEND_TIMEOUT_SECONDS = 5.0
SPAWN_TIMEOUT_SECONDS = 60.0
_REGISTER_MARKER = "SENTINEL_REGISTER:"
_ROLE_PATTERN = re.compile(r"^[a-z0-9_-]+$")

Entry = dict[str, Any]


def project_root() -> Path:
    override = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[1]


def mailbox_dir(root: Path) -> Path:
    return root / ".claude" / "mailbox"


def registry_path(root: Path) -> Path:
    return mailbox_dir(root) / "registry.json"


def claude_config_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if override:
        return Path(override).resolve()
    return Path.home() / ".claude"


def sessions_dir() -> Path:
    return claude_config_dir() / "sessions"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_pid(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _string_map(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _read_json(path: Path) -> Entry | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _string_map(data) or None


def _write_json(path: Path, data: Entry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temp, path)


def load_registry(root: Path) -> Entry:
    data = _read_json(registry_path(root)) or {}
    updated_at = data.get("updated_at")
    return {
        "version": 1,
        "updated_at": updated_at if isinstance(updated_at, str) else _now_iso(),
        "roles": _string_map(data.get("roles")),
        "sessions": _string_map(data.get("sessions")),
    }


def save_registry(root: Path, data: Entry) -> None:
    payload = {
        "version": 1,
        "updated_at": _now_iso(),
        "roles": _string_map(data.get("roles")),
        "sessions": _string_map(data.get("sessions")),
    }
    _write_json(registry_path(root), payload)


def _update_registry(root: Path, mutate: Callable[[Entry], None]) -> Entry:
    data = load_registry(root)
    mutate(data)
    save_registry(root, data)
    return data


def pid_alive(pid: object) -> bool:
    value = _as_pid(pid)
    if value is None or value <= 0:
        return False
    try:
        os.kill(value, 0)
    except OSError:
        return False
    return True


def _iter_session_records() -> Iterator[Entry]:
    directory = sessions_dir()
    if not directory.is_dir():
        return
    for path in directory.glob("*.json"):
        data = _read_json(path)
        if data is not None:
            yield data


def session_record_by_pid(pid: int) -> Entry | None:
    for record in _iter_session_records():
        if _as_pid(record.get("pid")) == pid:
            return record
    return None


def _read_peer_token(pid: int) -> str | None:
    directory = sessions_dir()
    if not directory.is_dir() or pid <= 0:
        return None
    for path in sorted(directory.glob(f"{pid}.*.key")):
        data = _read_json(path)
        token = data.get("peerToken") if data is not None else None
        if isinstance(token, str) and token:
            return token
    return None


def _build_pipe_payload(token: str, text: str) -> bytes:
    auth = json.dumps(
        {"type": "auth", "token": token},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    frame = json.dumps(
        {"type": "user", "message": {"role": "user", "content": text}},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{auth}\n{frame}\n".encode()


def _send_over_pipe(socket_path: str, token: str, text: str) -> None:
    with open(socket_path, "wb", buffering=0) as stream:
        stream.write(_build_pipe_payload(token, text))


def _send_with_timeout(socket_path: str, token: str, text: str) -> tuple[bool, str]:
    outcome: Entry = {"ok": False, "reason": "timeout"}

    def _work() -> None:
        try:
            _send_over_pipe(socket_path, token, text)
        except Exception as exc:  # noqa: BLE001 - 投递失败由调用方决定是否降级
            outcome["reason"] = f"{type(exc).__name__}: {exc}"
        else:
            outcome["ok"] = True
            outcome["reason"] = ""

    thread = threading.Thread(target=_work, daemon=True)
    thread.start()
    thread.join(SEND_TIMEOUT_SECONDS)
    if thread.is_alive():
        return False, "timeout"
    return bool(outcome["ok"]), str(outcome["reason"])


def deliver_peer_message(record: Entry, text: str) -> tuple[bool, str]:
    socket_path = record.get("messagingSocketPath")
    pid = _as_pid(record.get("pid"))
    if not isinstance(socket_path, str) or not socket_path:
        return False, "no-socket"
    if pid is None:
        return False, "no-pid"
    token = _read_peer_token(pid)
    if token is None:
        return False, "no-token"
    return _send_with_timeout(socket_path, token, text)


def spawn_role_window(root: Path, role: str) -> Entry | None:
    """调用冷启动脚本只拉一个角色窗口；返回脚本回报的 {session, ref, pid}。"""
    if os.name != "nt":
        return None
    script = root / "scripts" / "dev_start.ps1"
    if not script.is_file():
        return None
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Role",
        role,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SPAWN_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in reversed(completed.stdout.splitlines()):
        if _REGISTER_MARKER not in line:
            continue
        raw = line.split(_REGISTER_MARKER, 1)[1].strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and _as_pid(data.get("pid")) is not None:
            return _string_map(data)
    return None


def register_role(root: Path, *, role: str, session: str = "", ref: str = "", pid: int) -> Entry:
    role = role.strip().casefold()
    if not role:
        raise ValueError("role 不能为空")
    if _ROLE_PATTERN.fullmatch(role) is None:
        raise ValueError(f"非法角色名：{role}")
    entry: Entry = {
        "session": session.strip(),
        "ref": ref.strip(),
        "pid": int(pid),
        "updated_at": _now_iso(),
    }

    def mutate(data: Entry) -> None:
        roles = _string_map(data.get("roles"))
        roles[role] = entry
        data["roles"] = roles

    _update_registry(root, mutate)
    return dict(entry)


def resolve_role(root: Path, role: str) -> Entry | None:
    role = role.strip().casefold()
    data = load_registry(root)
    entry = _string_map(data.get("roles")).get(role)
    if not isinstance(entry, dict):
        return None
    result = _string_map(entry)
    result["role"] = role
    result["alive"] = pid_alive(entry.get("pid"))
    return result


def _is_target_file(value: str) -> bool:
    normalized = value.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if os.name == "nt":
        return normalized.casefold() == TARGET_FILE.casefold()
    return normalized == TARGET_FILE


def _sender_role(data: Entry, session_id: str) -> str:
    sessions = _string_map(data.get("sessions"))
    if session_id:
        mapped = sessions.get(session_id)
        if isinstance(mapped, str) and mapped:
            return mapped
    env_role = os.environ.get("SENTINEL_ROLE", "").strip().casefold()
    return env_role


def _remember_session(root: Path, data: Entry, session_id: str, role: str) -> Entry:
    if not session_id:
        return data
    sessions = _string_map(data.get("sessions"))
    if sessions.get(session_id) == role:
        return data

    def mutate(current: Entry) -> None:
        current_sessions = _string_map(current.get("sessions"))
        current_sessions[session_id] = role
        current["sessions"] = current_sessions

    return _update_registry(root, mutate)


def notify(root: Path, *, file: str, session_id: str = "") -> Entry:
    """Hook 唯一入口：解析发送方 → 对端 → 存活投递或降级新建。

    所有可预期失败都转成返回值，不向上抛异常；Hook 侧会再兜底吞掉异常。
    """
    try:
        if not _is_target_file(file):
            return {"action": "noop", "reason": "non-target"}
        data = load_registry(root)
        role = _sender_role(data, session_id)
        if role not in PEER_BY_ROLE:
            return {"action": "noop", "reason": "unknown-sender"}
        data = _remember_session(root, data, session_id, role)
        target_role = PEER_BY_ROLE[role]
        target = _string_map(data.get("roles")).get(target_role)
        if isinstance(target, dict) and pid_alive(target.get("pid")):
            target_pid = _as_pid(target.get("pid"))
            target_record = session_record_by_pid(target_pid) if target_pid is not None else None
            if target_record is not None:
                delivered, reason = deliver_peer_message(
                    target_record, WAKE_TEXT_TEMPLATE.format(role=role)
                )
                if delivered:
                    return {"action": "delivered", "role": target_role, "pid": target_pid}
                return {"action": "delivery-failed", "role": target_role, "reason": reason}
        launched = spawn_role_window(root, target_role)
        if launched is None:
            return {"action": "spawn-failed", "role": target_role}
        launched_pid = _as_pid(launched.get("pid"))
        registration = register_role(
            root,
            role=target_role,
            session=str(launched.get("session") or ""),
            ref=str(launched.get("ref") or ""),
            pid=launched_pid if launched_pid is not None else 0,
        )
        return {"action": "spawned", "role": target_role, "registration": registration}
    except (OSError, ValueError, TimeoutError) as exc:
        return {"action": "error", "reason": str(exc)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wake", description="跨会话唤醒（registry + 本机管道）")
    subparsers = parser.add_subparsers(dest="command", required=True)

    notify_parser = subparsers.add_parser("notify", help="Hook 入口：交流区变更后唤醒对端")
    notify_parser.add_argument("--file", required=True)
    notify_parser.add_argument("--session-id", default="")

    register_parser = subparsers.add_parser("register", help="冷启动脚本登记角色窗口")
    register_parser.add_argument("--role", required=True)
    register_parser.add_argument("--session", default="")
    register_parser.add_argument("--ref", default="")
    register_parser.add_argument("--pid", type=int, required=True)

    resolve_parser = subparsers.add_parser("resolve", help="调试：解析角色窗口")
    resolve_parser.add_argument("--role", required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = project_root()
    if args.command == "notify":
        session_id = args.session_id or os.environ.get("SENTINEL_SESSION_ID", "").strip()
        result = notify(root, file=args.file, session_id=session_id)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    if args.command == "register":
        try:
            registration = register_role(
                root,
                role=args.role,
                session=args.session,
                ref=args.ref,
                pid=args.pid,
            )
        except (OSError, ValueError) as exc:
            print(f"wake: {exc}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {"registered": True, "registration": registration},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0
    if args.command == "resolve":
        target = resolve_role(root, args.role)
        if target is None:
            print("{}")
            return 1
        print(json.dumps(target, ensure_ascii=False, separators=(",", ":")))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
