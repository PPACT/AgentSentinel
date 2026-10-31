"""H4：PII / 密钥夹具守卫（PreToolUse / Edit|Write|MultiEdit|NotebookEdit）。

写入前扫描 ``content`` / ``new_string`` / ``new_source``；真实密钥与 PII 一律 DENY。
合法假数据由 ``common.WHITELIST_PATTERNS`` 与 ``# secret-fixture`` 标记放行。
"""

from __future__ import annotations

import common


def main() -> None:
    payload = common.read_payload()
    if payload.get("tool_name") not in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        return
    tool_in = common.tool_input(payload)
    raw_path = tool_in.get("file_path") or tool_in.get("notebook_path")
    path = common.relative_project_path(raw_path, payload)
    # 安全拦截测试文件整体放行：文件名以 test_guard 开头，避免写夹具时反复拦截。
    if common.is_security_fixture_path(path):
        return
    for text in common.content_texts(tool_in):
        # 文件头部声明 # secret-fixture 时，整个文件视为已人工确认的夹具。
        if common.has_fixture_file_marker(text):
            continue
        reason = common.scan_secrets(text)
        if reason is not None:
            common.emit_decision(
                "deny",
                "检测到真实密钥或 PII；测试夹具请改用合成值并加 # secret-fixture 标记"
                f"（命中：{reason}）",
            )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 安全钩子异常一律 DENY
        common.fail_closed(f"H4 执行异常，按 DENY 处理：{exc}")
