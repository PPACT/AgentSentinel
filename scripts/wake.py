"""跨会话唤醒信箱：写入、去重、防抖、标记与归档（仅标准库）。

信箱位置：``<project>/.claude/mailbox/inbox.jsonl``（gitignored）。
每条只存指针（topic / event / commit / path / 状态），不存文件正文。

子命令：
    post --from --to --topic --event [--commit] [--path]
    mark-notified --id
    mark-read --id
    tail [--limit 10]
    prune

``slots.json`` 缺失或解析不到对端时静默降级：只写信箱，不报错、不阻塞。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEBOUNCE_SECONDS = 5.0
MAX_INBOX_LINES = 50
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_STALE_SECONDS = 30.0
ROLE_STALE_SECONDS = 600.0
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"
_ROLE_PATTERN = re.compile(r"^[a-z0-9_-]+$")

Entry = dict[str, Any]


def project_root() -> Path:
    override = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[1]


def mailbox_dir(root: Path) -> Path:
    return root / ".claude" / "mailbox"


def inbox_path(root: Path) -> Path:
    return mailbox_dir(root) / "inbox.jsonl"


def archive_path(root: Path) -> Path:
    return mailbox_dir(root) / "archive.jsonl"


def _now() -> datetime:
    return datetime.now(UTC)


def _format_ts(moment: datetime) -> str:
    return moment.strftime(_TS_FORMAT)


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, _TS_FORMAT)
    except ValueError:
        return None


def _file_hash(path: Path) -> str:
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""
    return digest


def _entry_key(topic: str, event: str, commit: str, file_hash: str) -> str:
    seed = f"{topic}\x1f{event}\x1f{commit}\x1f{file_hash}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _read_entries(path: Path) -> list[Entry]:
    if not path.is_file():
        return []
    entries: list[Entry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            entries.append(dict(data))
    return entries


def _write_entries(path: Path, entries: list[Entry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        for entry in entries:
            stream.write(f"{json.dumps(entry, ensure_ascii=False, separators=(',', ':'))}\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _append_entries(path: Path, entries: list[Entry]) -> None:
    if not entries:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        for entry in entries:
            stream.write(f"{json.dumps(entry, ensure_ascii=False, separators=(',', ':'))}\n")
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    directory = mailbox_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".lock"
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                stale = time.time() - lock_path.stat().st_mtime > LOCK_STALE_SECONDS
            except OSError:
                stale = False
            if stale:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"信箱锁超时：{lock_path}")
            time.sleep(0.05)
            continue
        os.close(descriptor)
        break
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def roles_dir(root: Path) -> Path:
    return mailbox_dir(root) / "roles"


def role_registration_path(root: Path, role: str) -> Path:
    return roles_dir(root) / f"{role}.json"


def role_lock_file(root: Path, role: str) -> Path:
    return roles_dir(root) / f"{role}.lock"


def current_role(_root: Path) -> str:
    """角色来源仅进程级 ``SENTINEL_ROLE``；共享 session-role 文件已废弃。"""
    return os.environ.get("SENTINEL_ROLE", "").strip().casefold()


def _read_json(path: Path) -> Entry | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return dict(data) if isinstance(data, dict) else None


def _write_json(path: Path, data: Entry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _role_is_stale(registration: Entry, now: datetime) -> bool:
    heartbeat = _parse_ts(registration.get("heartbeat_at"))
    if heartbeat is None:
        return True
    return (now - heartbeat).total_seconds() > ROLE_STALE_SECONDS


class RegistrationConflict(RuntimeError):
    """同角色已有活跃注册；调用方据此提示现有窗口并要求接管确认。"""

    def __init__(self, message: str, active: Entry) -> None:
        super().__init__(message)
        self.active = active


def _claim_role_lock(path: Path, info: Entry, *, force: bool) -> None:
    if force:
        _write_json(path, info)
        return
    payload = json.dumps(info, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        active = _read_json(path) or {}
        raise RegistrationConflict(
            f"角色锁已被 {active.get('session', '<unknown>')} 占用",
            active,
        ) from None
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def register_role(
    root: Path,
    *,
    role: str,
    session: str,
    ref: str = "",
    force: bool = False,
) -> Entry:
    """注册窗口身份；同角色活跃注册未加 --force 时拒绝。"""
    role = role.strip().casefold()
    session = session.strip()
    ref = ref.strip()
    if not role or not session:
        raise ValueError("role 与 session 不能为空")
    if _ROLE_PATTERN.fullmatch(role) is None:
        raise ValueError(f"非法角色名：{role}")
    now = _now()
    with _locked(root):
        roles_dir(root).mkdir(parents=True, exist_ok=True)
        registration_path = role_registration_path(root, role)
        existing = _read_json(registration_path)
        active = existing is not None and not _role_is_stale(existing, now)
        if active and not force and existing is not None:
            raise RegistrationConflict(
                f"角色 {role} 已被 {existing.get('session', '<unknown>')} 占用"
                f"（启动于 {existing.get('started_at', '<unknown>')}）",
                existing,
            )
        same_window = (
            existing is not None
            and existing.get("session") == session
            and existing.get("ref", "") == ref
        )
        started_at = existing.get("started_at") if same_window and existing else None
        info: Entry = {
            "role": role,
            "session": session,
            "ref": ref,
            "pid": os.getpid(),
            "started_at": started_at if isinstance(started_at, str) else _format_ts(now),
            "heartbeat_at": _format_ts(now),
        }
        lock_path = role_lock_file(root, role)
        if lock_path.exists() and (force or not active):
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
        _claim_role_lock(lock_path, info, force=force)
        _write_json(registration_path, info)
    return info


def refresh_heartbeat(root: Path, role: str) -> Entry | None:
    """刷新指定角色注册的心跳；无注册时返回 None。"""
    role = role.strip().casefold()
    if not role:
        return None
    with _locked(root):
        path = role_registration_path(root, role)
        registration = _read_json(path)
        if registration is None:
            return None
        registration["heartbeat_at"] = _format_ts(_now())
        _write_json(path, registration)
        lock_path = role_lock_file(root, role)
        if lock_path.exists():
            _write_json(lock_path, registration)
    return registration


def _refresh_current(root: Path) -> None:
    """当前角色有注册时顺带刷新心跳；辅助动作失败不打断主命令。"""
    role = current_role(root)
    if not role:
        return
    try:
        refresh_heartbeat(root, role)
    except (OSError, TimeoutError):
        return


def whoami_info(root: Path) -> Entry:
    """返回当前角色与注册状态；无角色或无注册时 status=inactive。"""
    role = current_role(root)
    if not role:
        return {"role": None, "registration": None, "stale": True, "status": "inactive"}
    registration = _read_json(role_registration_path(root, role))
    if registration is None:
        return {"role": role, "registration": None, "stale": True, "status": "inactive"}
    stale = _role_is_stale(registration, _now())
    return {
        "role": role,
        "registration": registration,
        "stale": stale,
        "status": "inactive" if stale else "active",
    }


def _slots_target(root: Path, role: str) -> tuple[str, str] | None:
    slots_path = mailbox_dir(root) / "slots.json"
    try:
        raw = json.loads(slots_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    value = raw.get(role)
    if isinstance(value, str) and value.strip():
        return value.strip(), ""
    if isinstance(value, dict):
        ref = value.get("ref")
        for key in ("session", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip(), ref.strip() if isinstance(ref, str) else ""
    return None


def resolve_role(root: Path, role: str) -> Entry | None:
    """解析角色对应的窗口：过期注册仍可寻址；无记录才回退 slots.json。"""
    role = role.strip().casefold()
    registration = _read_json(role_registration_path(root, role))
    if registration is not None:
        session = registration.get("session")
        ref = registration.get("ref", "")
        if isinstance(session, str) and session:
            stale = _role_is_stale(registration, _now())
            return {
                "role": role,
                "session": session,
                "ref": ref if isinstance(ref, str) else "",
                "stale": stale,
                "source": "roles-stale" if stale else "roles",
            }
    slots = _slots_target(root, role)
    if slots is not None:
        return {"role": role, "session": slots[0], "ref": slots[1], "source": "slots"}
    return None


def resolve_peer(root: Path, role: str) -> str | None:
    """解析角色对应的会话名；注册表与 slots.json 都没有时返回 None。"""
    target = resolve_role(root, role)
    if target is None:
        return None
    session = target.get("session")
    return session if isinstance(session, str) else None


def _slot_entries(entries: list[Entry], sender: str, recipient: str) -> list[int]:
    return [
        index
        for index, entry in enumerate(entries)
        if entry.get("from") == sender and entry.get("to") == recipient
    ]


def last_hash(root: Path, *, sender: str, recipient: str, rel_path: str) -> str | None:
    """返回同一 slot、同一路径最近一条记录的 file_hash；没有则 None。"""
    entries = _read_entries(inbox_path(root))
    for index in reversed(_slot_entries(entries, sender, recipient)):
        entry = entries[index]
        if entry.get("path") == rel_path:
            value = entry.get("file_hash")
            return value if isinstance(value, str) else None
    return None


def post_event(
    root: Path,
    *,
    sender: str,
    recipient: str,
    topic: str,
    event: str,
    commit: str = "",
    rel_path: str = "",
    file_hash: str = "",
    actionable: bool = True,
) -> Entry:
    """写入一条指针事件；同 slot 5 秒内仅合并被动记录，重复未读键丢弃。"""
    sender = sender.strip().casefold() or "unknown"
    recipient = recipient.strip().casefold() or "unknown"
    topic = topic.strip()
    event = event.strip()
    if not topic or not event:
        raise ValueError("topic 与 event 不能为空")
    key = _entry_key(topic, event, commit, file_hash)
    now = _now()
    with _locked(root):
        path = inbox_path(root)
        entries = _read_entries(path)
        slot_indexes = _slot_entries(entries, sender, recipient)
        merged = False
        posted = False
        dropped = False
        entry: Entry
        if slot_indexes:
            previous = entries[slot_indexes[-1]]
            previous_ts = _parse_ts(previous.get("ts"))
            recent = (
                previous_ts is not None and (now - previous_ts).total_seconds() <= DEBOUNCE_SECONDS
            )
            # 只有双方都是被动记录时才合并；actionable 事件不被覆盖。
            if (
                recent
                and not actionable
                and not previous.get("actionable", True)
                and previous.get("status") == "unread"
                and not previous.get("notified")
            ):
                entry = previous
                entry.update(
                    {
                        "ts": _format_ts(now),
                        "topic": topic,
                        "event": event,
                        "commit": commit,
                        "path": rel_path,
                        "file_hash": file_hash,
                        "dedupe_key": key,
                        "actionable": False,
                    }
                )
                merged = True
        if not merged:
            duplicate = next(
                (
                    item
                    for item in entries
                    if item.get("dedupe_key") == key and item.get("status") == "unread"
                ),
                None,
            )
            if duplicate is not None:
                entry = duplicate
                dropped = True
            else:
                entry = {
                    "id": uuid.uuid4().hex[:8],
                    "ts": _format_ts(now),
                    "from": sender,
                    "to": recipient,
                    "topic": topic,
                    "event": event,
                    "commit": commit,
                    "path": rel_path,
                    "file_hash": file_hash,
                    "dedupe_key": key,
                    "hop": 1,
                    "actionable": actionable,
                    "status": "unread",
                    "notified": False,
                    "notify_attempts": 0,
                }
                entries.append(entry)
                posted = True
        if posted or merged:
            _write_entries(path, entries)
        unread = sum(
            1 for item in entries if item.get("to") == recipient and item.get("status") == "unread"
        )
    peer = resolve_peer(root, recipient)
    payload = (
        f"[唤醒] 信箱 unread={unread} topic={topic} event={event}；"
        "先读 STATUS 头与信箱尾部，再决定接单。"
    )
    return {
        "entry": entry,
        "peer": peer,
        "payload": payload,
        "posted": posted,
        "merged": merged,
        "deduped": dropped,
    }


def _find(entries: list[Entry], entry_id: str) -> Entry:
    for entry in entries:
        if entry.get("id") == entry_id:
            return entry
    raise KeyError(f"信箱中不存在 id={entry_id}")


def mark_notified(root: Path, entry_id: str) -> Entry:
    with _locked(root):
        path = inbox_path(root)
        entries = _read_entries(path)
        entry = _find(entries, entry_id)
        entry["notified"] = True
        _write_entries(path, entries)
    return entry


def mark_read(root: Path, entry_id: str) -> Entry:
    with _locked(root):
        path = inbox_path(root)
        entries = _read_entries(path)
        entry = _find(entries, entry_id)
        entry["status"] = "read"
        entry["read_at"] = _format_ts(_now())
        _write_entries(path, entries)
    return entry


def unnotified_from(root: Path, role: str) -> list[Entry]:
    """返回本会话（from=role）尚未通知的可执行条目；被动记录不触发唤醒。"""
    return [
        entry
        for entry in _read_entries(inbox_path(root))
        if entry.get("from") == role and not entry.get("notified") and entry.get("actionable", True)
    ]


def bump_notify_attempts(root: Path, entry_ids: Sequence[str]) -> None:
    """记录 Stop Hook 的阻断次数，用于同一 id 最多阻断 2 次。"""
    wanted = set(entry_ids)
    with _locked(root):
        path = inbox_path(root)
        entries = _read_entries(path)
        for entry in entries:
            if entry.get("id") in wanted:
                attempts = entry.get("notify_attempts", 0)
                entry["notify_attempts"] = attempts + 1 if isinstance(attempts, int) else 1
        _write_entries(path, entries)


def tail_entries(root: Path, limit: int = 10) -> list[Entry]:
    if limit < 0:
        raise ValueError("limit 不能为负数")
    entries = _read_entries(inbox_path(root))
    return entries[-limit:] if limit else []


def prune_entries(root: Path) -> Entry:
    """只归档已读且已通知条目；未读/未通知条目即使超限也保留并给出 warning。"""
    with _locked(root):
        path = inbox_path(root)
        entries = _read_entries(path)
        archived_indexes = {
            index
            for index, entry in enumerate(entries)
            if entry.get("status") == "read" and entry.get("notified")
        }
        archived = [entry for index, entry in enumerate(entries) if index in archived_indexes]
        remaining = [entry for index, entry in enumerate(entries) if index not in archived_indexes]
        _append_entries(archive_path(root), archived)
        _write_entries(path, remaining)
    result: Entry = {"archived": len(archived), "remaining": len(remaining)}
    if len(remaining) > MAX_INBOX_LINES:
        result["warning"] = (
            f"inbox 仍有 {len(remaining)} 行，超过 {MAX_INBOX_LINES}；"
            "未读/未通知条目已保留，未做归档"
        )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wake", description="跨会话唤醒信箱")
    subparsers = parser.add_subparsers(dest="command", required=True)

    post = subparsers.add_parser("post", help="写入一条指针事件")
    post.add_argument("--from", dest="sender", required=True)
    post.add_argument("--to", dest="recipient", required=True)
    post.add_argument("--topic", required=True)
    post.add_argument("--event", required=True)
    post.add_argument("--commit", default="")
    post.add_argument("--path", default="")
    post.add_argument(
        "--passive",
        action="store_true",
        help="被动记录（actionable=false），不触发 Stop Hook 唤醒",
    )

    notified = subparsers.add_parser("mark-notified", help="标记已通知")
    notified.add_argument("--id", required=True)

    read = subparsers.add_parser("mark-read", help="标记已读")
    read.add_argument("--id", required=True)

    tail = subparsers.add_parser("tail", help="输出最近条目（JSONL）")
    tail.add_argument("--limit", type=int, default=10)

    register = subparsers.add_parser("register", help="注册当前窗口的角色身份")
    register.add_argument("--role", required=True)
    register.add_argument("--session", required=True)
    register.add_argument("--ref", default="")
    register.add_argument("--force", action="store_true", help="接管同角色已有活跃注册")

    subparsers.add_parser("whoami", help="输出当前角色与注册状态")

    heartbeat = subparsers.add_parser("heartbeat", help="刷新当前角色心跳")
    heartbeat.add_argument("--role", default="")

    resolve = subparsers.add_parser("resolve", help="解析角色对应的窗口")
    resolve.add_argument("--role", required=True)

    subparsers.add_parser("prune", help="归档已读并控制行数")
    return parser


def _emit_entry(entry: Entry) -> None:
    print(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = project_root()
    try:
        if args.command == "post":
            _refresh_current(root)
            file_hash = ""
            if args.path:
                candidate = Path(args.path)
                if not candidate.is_absolute():
                    candidate = root / candidate
                file_hash = _file_hash(candidate)
            result = post_event(
                root,
                sender=args.sender,
                recipient=args.recipient,
                topic=args.topic,
                event=args.event,
                commit=args.commit,
                rel_path=args.path,
                file_hash=file_hash,
                actionable=not args.passive,
            )
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        elif args.command == "mark-notified":
            print(
                json.dumps(mark_notified(root, args.id), ensure_ascii=False, separators=(",", ":"))
            )
        elif args.command == "mark-read":
            print(json.dumps(mark_read(root, args.id), ensure_ascii=False, separators=(",", ":")))
        elif args.command == "tail":
            _refresh_current(root)
            for entry in tail_entries(root, args.limit):
                _emit_entry(entry)
        elif args.command == "prune":
            print(json.dumps(prune_entries(root), ensure_ascii=False, separators=(",", ":")))
        elif args.command == "register":
            try:
                registration = register_role(
                    root,
                    role=args.role,
                    session=args.session,
                    ref=args.ref,
                    force=args.force,
                )
            except RegistrationConflict as exc:
                print(
                    json.dumps(
                        {"registered": False, "reason": str(exc), "active": exc.active},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
                return 1
            print(
                json.dumps(
                    {"registered": True, "registration": registration},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        elif args.command == "whoami":
            _refresh_current(root)
            print(json.dumps(whoami_info(root), ensure_ascii=False, separators=(",", ":")))
        elif args.command == "heartbeat":
            role = (args.role or current_role(root)).strip().casefold()
            refreshed = refresh_heartbeat(root, role) if role else None
            print(
                json.dumps(
                    {
                        "refreshed": refreshed is not None,
                        "role": role or None,
                        "registration": refreshed,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        elif args.command == "resolve":
            target = resolve_role(root, args.role)
            if target is None:
                print("{}")
                return 1
            print(json.dumps(target, ensure_ascii=False, separators=(",", ":")))
    except (KeyError, ValueError, OSError, TimeoutError) as exc:
        print(f"wake: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
