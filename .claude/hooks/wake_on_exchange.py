"""PostToolUse：交流区文件被写入后静默唤醒对端（极简唤醒 v2）。

只处理 ``docs/local/交流区/交流区.md``；其他工具、其他路径、任何异常一律
静默退出 0，且不输出任何内容，避免污染 Claude Code 的工具输出。
"""

from __future__ import annotations

import os
from typing import Any

import common
from scripts import wake

TARGET_FILE = "docs/local/交流区/交流区.md"
_WRITE_TOOLS = {"Edit", "Write", "MultiEdit"}


def _is_target(path: str | None) -> bool:
    if path is None:
        return False
    normalized = path.replace("\\", "/")
    if os.name == "nt":
        return normalized.casefold() == TARGET_FILE.casefold()
    return normalized == TARGET_FILE


def handle(payload: dict[str, Any]) -> dict[str, Any] | None:
    """处理一条 Hook 输入；返回 notify 结果供测试观察，不产生任何输出。"""
    if payload.get("tool_name") not in _WRITE_TOOLS:
        return None
    tool_in = common.tool_input(payload)
    rel_path = common.relative_project_path(tool_in.get("file_path"), payload)
    if not _is_target(rel_path):
        return None
    session_id = payload.get("session_id")
    return wake.notify(
        common.project_root(),
        file=str(rel_path),
        session_id=session_id if isinstance(session_id, str) else "",
    )


def main() -> None:
    try:
        handle(common.read_payload())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001, S110 - 唤醒 Hook 失败不得阻断工具调用
        pass


if __name__ == "__main__":
    main()
