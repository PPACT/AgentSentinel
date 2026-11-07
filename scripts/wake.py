"""跨会话唤醒 v3：提示词即注册 + registry 查表（仅标准库）。

Agent 收到含角色的唤醒提示词后执行 ``register``，登记本会话的 name/ref；
需要交接时读取 ``.claude/mailbox/registry.json``，再用原生 ``SendMessage``
发指针。本模块**不**读取项目外路径、**不**探测进程、**不**发送消息、**不**
启动子进程。

子命令：

    register --role <role> [--session <name>] [--ref <ref>]
    resolve  --role <role>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROLE_PATTERN = re.compile(r"^[a-z0-9_-]+$")
_LOCK_TIMEOUT_SECONDS = 3.0
_LOCK_POLL_SECONDS = 0.05
_LOCK_STALE_SECONDS = 10.0

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


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    """原子替换：先写同目录临时文件，再 ``os.replace`` 覆盖正式文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )
    try:
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
        raise


@contextmanager
def _registry_lock(root: Path) -> Iterator[None]:
    """最小文件锁：避免并发 register 交错写入；超过 10 秒的锁视为残留。"""
    lock = mailbox_dir(root) / "registry.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > _LOCK_STALE_SECONDS
            except OSError:
                stale = False
            if stale:
                lock.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError("registry 锁等待超时")
            time.sleep(_LOCK_POLL_SECONDS)
            continue
        try:
            yield
        finally:
            os.close(descriptor)
            lock.unlink(missing_ok=True)
        return


def load_registry(root: Path) -> Entry:
    data = _read_json(registry_path(root)) or {}
    updated_at = data.get("updated_at")
    return {
        "version": 1,
        "updated_at": updated_at if isinstance(updated_at, str) else _now_iso(),
        "roles": _string_map(data.get("roles")),
    }


def save_registry(root: Path, data: Entry) -> None:
    payload = {
        "version": 1,
        "updated_at": _now_iso(),
        "roles": _string_map(data.get("roles")),
    }
    _write_json(registry_path(root), payload)


def _normalize_role(role: str) -> str:
    normalized = role.strip().casefold()
    if not normalized:
        raise ValueError("role 不能为空")
    if _ROLE_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"非法角色名：{normalized}")
    return normalized


def register_role(root: Path, *, role: str, session: str = "", ref: str = "") -> Entry:
    """登记（或覆盖）一个角色的会话 name/ref；registry 只保留 ``roles``。"""
    normalized = _normalize_role(role)
    entry: Entry = {
        "session": session.strip(),
        "ref": ref.strip(),
        "updated_at": _now_iso(),
    }
    with _registry_lock(root):
        data = load_registry(root)
        roles = _string_map(data.get("roles"))
        roles[normalized] = entry
        data["roles"] = roles
        save_registry(root, data)
    return {"role": normalized, **entry}


def resolve_role(root: Path, role: str) -> Entry | None:
    normalized = _normalize_role(role)
    data = load_registry(root)
    entry = _string_map(data.get("roles")).get(normalized)
    if not isinstance(entry, dict):
        return None
    result = _string_map(entry)
    result["role"] = normalized
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wake", description="跨会话唤醒（提示词即注册）")
    subparsers = parser.add_subparsers(dest="command", required=True)

    register_parser = subparsers.add_parser("register", help="登记本会话的角色 name/ref")
    register_parser.add_argument("--role", required=True)
    register_parser.add_argument("--session", default="")
    register_parser.add_argument("--ref", default="")

    resolve_parser = subparsers.add_parser("resolve", help="调试：解析角色会话")
    resolve_parser.add_argument("--role", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = project_root()
    if args.command == "register":
        try:
            registration = register_role(
                root,
                role=args.role,
                session=args.session,
                ref=args.ref,
            )
        except (OSError, ValueError, TimeoutError) as exc:
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
        try:
            target = resolve_role(root, args.role)
        except ValueError as exc:
            print(f"wake: {exc}", file=sys.stderr)
            return 1
        if target is None:
            print("{}")
            return 1
        print(json.dumps(target, ensure_ascii=False, separators=(",", ":")))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
