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
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"

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


def resolve_peer(root: Path, role: str) -> str | None:
    """从 slots.json 解析角色对应的会话名；缺失或无效时返回 None。"""
    slots_path = mailbox_dir(root) / "slots.json"
    try:
        raw = json.loads(slots_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    value = raw.get(role)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("session", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


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
) -> Entry:
    """写入一条指针事件；同 slot 5 秒内合并，重复未读键丢弃。"""
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
            if recent and previous.get("status") == "unread" and not previous.get("notified"):
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
    """返回本会话（from=role）尚未通知的条目。"""
    return [
        entry
        for entry in _read_entries(inbox_path(root))
        if entry.get("from") == role and not entry.get("notified")
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
    """把已读且已通知条目移入 archive；剩余仍超限时继续归档最旧条目。"""
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
        overflow = max(len(remaining) - MAX_INBOX_LINES, 0)
        if overflow:
            archived.extend(remaining[:overflow])
            remaining = remaining[overflow:]
        _append_entries(archive_path(root), archived)
        _write_entries(path, remaining)
    return {
        "archived": len(archived),
        "remaining": len(remaining),
        "overflow_archived": overflow,
    }


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

    notified = subparsers.add_parser("mark-notified", help="标记已通知")
    notified.add_argument("--id", required=True)

    read = subparsers.add_parser("mark-read", help="标记已读")
    read.add_argument("--id", required=True)

    tail = subparsers.add_parser("tail", help="输出最近条目（JSONL）")
    tail.add_argument("--limit", type=int, default=10)

    subparsers.add_parser("prune", help="归档已读并控制行数")
    return parser


def _emit_entry(entry: Entry) -> None:
    print(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = project_root()
    try:
        if args.command == "post":
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
            )
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        elif args.command == "mark-notified":
            print(
                json.dumps(mark_notified(root, args.id), ensure_ascii=False, separators=(",", ":"))
            )
        elif args.command == "mark-read":
            print(json.dumps(mark_read(root, args.id), ensure_ascii=False, separators=(",", ":")))
        elif args.command == "tail":
            for entry in tail_entries(root, args.limit):
                _emit_entry(entry)
        elif args.command == "prune":
            print(json.dumps(prune_entries(root), ensure_ascii=False, separators=(",", ":")))
    except (KeyError, ValueError, OSError, TimeoutError) as exc:
        print(f"wake: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
