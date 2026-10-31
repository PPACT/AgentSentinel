"""H3：受保护文件写入拦截（PreToolUse / Edit|Write|MultiEdit|NotebookEdit）。

只拦 ``README.md``、``.env`` 系列与 ``AGENTS.md``；其余日常写入一律放行。
不做角色鉴权，角色行为由提示词约束。
"""

from __future__ import annotations

from pathlib import Path

import common

_PROTECTED_NAMES = {"README.md", "AGENTS.md"}


def _protected_reason(path: str) -> str | None:
    name = Path(path).name
    if name in _PROTECTED_NAMES:
        return f"{name} 为受保护文件，未经用户明确授权不得修改"
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return ".env 系列文件禁止编辑（.env.example 除外）"
    return None


def main() -> None:
    payload = common.read_payload()
    if payload.get("tool_name") not in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        return
    tool_in = common.tool_input(payload)
    raw_path = tool_in.get("file_path") or tool_in.get("notebook_path")
    path = common.relative_project_path(raw_path, payload)
    if path is None:
        return
    reason = _protected_reason(path)
    if reason is not None:
        common.emit_decision("deny", reason)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 安全钩子异常一律 DENY
        common.fail_closed(f"H3 执行异常，按 DENY 处理：{exc}")
