"""H4：业务目录 PII / 密钥写入扫描（PreToolUse / Edit|Write|MultiEdit|NotebookEdit）。

``tests/**`` 完全不扫描；只检查 ``src/**`` 与 ``agentsentinel/**``。
"""

from __future__ import annotations

import common

_BUSINESS_PREFIXES = ("agentsentinel/", "src/")


def main() -> None:
    payload = common.read_payload()
    if payload.get("tool_name") not in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        return
    tool_in = common.tool_input(payload)
    raw_path = tool_in.get("file_path") or tool_in.get("notebook_path")
    path = common.relative_project_path(raw_path, payload)
    if path is None or not path.startswith(_BUSINESS_PREFIXES):
        return
    for text in common.content_texts(tool_in):
        reason = common.scan_secrets(text, honor_markers=False)
        if reason is not None:
            common.emit_decision(
                "deny",
                f"业务目录禁止硬编码真实密钥或 PII（命中：{reason}）",
            )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 安全钩子异常一律 DENY
        common.fail_closed(f"H4 执行异常，按 DENY 处理：{exc}")
