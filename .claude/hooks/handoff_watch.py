"""通信区变更记录（PostToolUse / Edit|Write|MultiEdit|NotebookEdit）。

只记录、不唤醒：交流区或 STATUS 被写入后向信箱追加一条 ``changed`` 事件，
供本会话 Stop Hook 提示发送方在收尾前通知对端。非目标文件与任何异常一律
静默退出 0，不影响既有 Hook。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import common

_TARGET_FILES = {
    "docs/local/交流区/交流区.md",
    "docs/local/交流区/STATUS.md",
}
_WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_PEER_BY_ROLE = {
    "dev": "doc",
    "doc": "dev",
    "main": "dev",
}
_PEER_UNKNOWN = "unspecified"


def current_role(_root: Path) -> str:
    """角色来源仅进程级 SENTINEL_ROLE；共享 session-role 文件已废弃。"""
    return os.environ.get("SENTINEL_ROLE", "").strip().casefold()


def _changed_event(rel_path: str) -> tuple[str, str]:
    return "handoff", f"changed:{Path(rel_path).name}"


def main() -> None:
    payload = common.read_payload()
    if payload.get("tool_name") not in _WRITE_TOOLS:
        return
    tool_in = common.tool_input(payload)
    raw_path = tool_in.get("file_path") or tool_in.get("notebook_path")
    rel_path = common.relative_project_path(raw_path, payload)
    if rel_path is None or rel_path not in _TARGET_FILES:
        return

    root = common.project_root()
    target = root / rel_path
    try:
        file_hash = _sha256(target)
    except OSError:
        return

    from scripts import wake

    role = current_role(root)
    if not role:
        return
    try:
        wake.refresh_heartbeat(root, role)
    except Exception:  # noqa: BLE001, S110 - 心跳刷新失败不阻断记录
        pass
    peer = _PEER_BY_ROLE.get(role, _PEER_UNKNOWN)
    if wake.last_hash(root, sender=role, recipient=peer, rel_path=rel_path) == file_hash:
        return
    topic, event = _changed_event(rel_path)
    wake.post_event(
        root,
        sender=role,
        recipient=peer,
        topic=topic,
        event=event,
        rel_path=rel_path,
        file_hash=file_hash,
        actionable=False,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001, S110 - 记录型 Hook 失败不得阻断工具调用
        pass
