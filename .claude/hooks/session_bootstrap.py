"""SessionStart 兜底：有角色时静默登记，无角色时静默 no-op（v3）。

角色来源优先级：``SENTINEL_ROLE`` 环境变量 → ``.claude/mailbox/role.claim``
（首个非空行）。SessionStart 输入没有会话 name/ref，因此只能登记
``session_id``；完整寻址仍以唤醒提示词路径为准。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import common
from scripts import wake

CLAIM_PATH = Path(".claude") / "mailbox" / "role.claim"


def _claimed_role(root: Path) -> str:
    env_role = os.environ.get("SENTINEL_ROLE", "").strip()
    if env_role:
        return env_role
    try:
        text = (root / CLAIM_PATH).read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def handle(payload: dict[str, Any]) -> dict[str, Any] | None:
    """返回 register 结果供测试观察；不产生任何输出。"""
    root = common.project_root()
    role = _claimed_role(root)
    if not role:
        return None
    session_id = payload.get("session_id")
    session = session_id.strip() if isinstance(session_id, str) else ""
    try:
        return wake.register_role(root, role=role, session=session)
    except (OSError, ValueError, TimeoutError):
        return None


def main() -> None:
    try:
        handle(common.read_payload())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001, S110 - 兜底注册失败不得阻断会话
        pass


if __name__ == "__main__":
    main()
