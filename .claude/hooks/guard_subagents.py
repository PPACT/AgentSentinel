"""PreToolUse：禁止 Task / Agent 后台子代理（v3）。

角色间通信只允许「registry 查表 + 原生 SendMessage」；后台子代理会让角色
身份与唤醒链路失控。matcher 同时覆盖 ``Task`` 与 ``Agent`` 两个工具名。
"""

from __future__ import annotations

import common

DENIED_TOOLS = {"Task", "Agent"}


def main() -> None:
    payload = common.read_payload()
    if payload.get("tool_name") in DENIED_TOOLS:
        common.emit_decision(
            "deny",
            "禁止创建后台子代理：角色间通信只走 registry + SendMessage",
        )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 安全钩子异常一律 DENY
        common.fail_closed(f"子代理守卫执行异常，按 DENY 处理：{exc}")
