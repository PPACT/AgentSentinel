"""Stop：提示发送方在收尾前调用 SendMessage 通知对端。

发现本会话（``from=<当前角色>``）有未通知条目时阻断一次，reason 给出固定
模板（只发指针）；同一 id 最多阻断 2 次，之后放行并用 systemMessage 提醒用户。
输入无法识别、角色缺失或已处于 stop-hook 续跑状态时静默退出 0。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import common

MAX_BLOCKS = 2
_PEER_BY_ROLE = {
    "dev": "doc",
    "doc": "dev",
    "main": "dev",
}


def current_role(root: Path) -> str:
    env_role = os.environ.get("SENTINEL_ROLE", "").strip()
    if env_role:
        return env_role.casefold()
    try:
        marker = (root / ".claude" / "session-role").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return marker.casefold()


def _describe(entries: list[dict[str, Any]]) -> str:
    parts = [f"topic={entry.get('topic', '?')} id={entry.get('id', '?')}" for entry in entries[:3]]
    if len(entries) > 3:
        parts.append(f"等 {len(entries)} 条")
    return "；".join(parts)


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    try:
        payload = common.read_payload()
    except Exception:  # noqa: BLE001 - 输入无法识别时静默降级
        return
    if not isinstance(payload, dict) or payload.get("stop_hook_active"):
        return

    root = common.project_root()
    role = current_role(root)
    if not role:
        return

    from scripts import wake

    entries = wake.unnotified_from(root, role)
    if not entries:
        return

    pending = [entry for entry in entries if _attempts(entry) < MAX_BLOCKS]
    if pending:
        peer = _PEER_BY_ROLE.get(role, "<peer>")
        wake.bump_notify_attempts(root, [str(entry.get("id", "")) for entry in pending])
        reason = (
            f"信箱有 {len(entries)} 条未通知：{_describe(pending)}。"
            f"请调用 SendMessage(to={peer}) 发送固定模板，只发指针，"
            f"随后执行 uv run python scripts/wake.py mark-notified --id <id>。"
        )
        _emit({"decision": "block", "reason": reason})
        return

    exhausted = [entry for entry in entries if _attempts(entry) == MAX_BLOCKS]
    if not exhausted:
        return
    wake.bump_notify_attempts(root, [str(entry.get("id", "")) for entry in exhausted])
    _emit(
        {
            "systemMessage": (
                f"信箱仍有 {len(entries)} 条未通知且已阻断 {MAX_BLOCKS} 次，"
                "请手动检查 .claude/mailbox/inbox.jsonl 并通知对端。"
            )
        }
    )


def _attempts(entry: dict[str, Any]) -> int:
    value = entry.get("notify_attempts", 0)
    return value if isinstance(value, int) else 0


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001, S110 - Stop Hook 异常不得卡住会话收尾
        pass
