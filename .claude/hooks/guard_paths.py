"""H3：文件权责与 README 写保护（PreToolUse / Edit|Write|MultiEdit|NotebookEdit）。

角色来自 ``SENTINEL_ROLE`` 或 ``.claude/session-role``；缺失时对矩阵内路径降级 ASK。
"""

from __future__ import annotations

from typing import Any

import common

_ARCH_SPEC = "docs/多Agent研发流水线协作系统架构规范.md"
_CODE_PREFIXES = ("agentsentinel/", "tests/", ".claude/hooks/")
_CODE_FILES = {"pyproject.toml", ".claude/settings.json"}


def _is_code_path(path: str) -> bool:
    return path.startswith(_CODE_PREFIXES) or path in _CODE_FILES


def _decide(role: str | None, path: str) -> tuple[str, str] | None:
    if path == ".env" or (path.startswith(".env.") and path != ".env.example"):
        return ("deny", ".env 系列文件禁止任何角色编辑")
    if path == ".env.example":
        return ("allow", "")
    if path == "AGENTS.md" or path == _ARCH_SPEC:
        return ("deny", "用户专属文件只读")
    if path.startswith("docs/local/交流区/"):
        return ("allow", "")
    if path == "README.md":
        if role == "dev":
            return ("deny", "README 未经用户明确允许不得修改")
        if role == "doc":
            return ("ask", "README 修改需用户明确授权")
        return ("ask", "未识别角色：编辑 README 需用户确认")
    if path == "requirements.txt":
        if role == "dev":
            return ("ask", "requirements.txt 变更需用户当场确认")
        if role == "doc":
            return ("deny", "文档侧不得修改 requirements.txt")
        return ("ask", "未识别角色：编辑 requirements.txt 需用户确认")
    if _is_code_path(path):
        if role == "dev":
            return ("allow", "")
        if role == "doc":
            return ("deny", "文档侧不得修改代码与测试")
        return ("ask", "未识别角色：编辑代码需用户确认")
    if path.startswith("docs/"):
        if role == "doc":
            return ("allow", "")
        if role == "dev":
            return ("deny", "开发侧不得修改文档，请写交流区交文档侧")
        return ("ask", "未识别角色：编辑文档需用户确认")
    if path.startswith(".claude/agents/"):
        if role == "doc":
            return ("ask", "角色提示词修改需用户当场确认")
        if role == "dev":
            return ("deny", "开发侧不得修改角色提示词")
        return ("ask", "未识别角色：编辑角色提示词需用户确认")
    return None


def main() -> None:
    payload = common.read_payload()
    if payload.get("tool_name") not in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        return
    tool_in = common.tool_input(payload)
    raw_path: Any = tool_in.get("file_path") or tool_in.get("notebook_path")
    path = common.relative_project_path(raw_path, payload)
    if path is None:
        return
    role = common.detect_role(payload)
    # 安全拦截测试文件在角色缺失时不降级为 ASK，避免写夹具时反复弹窗。
    if role is None and common.is_security_fixture_path(path):
        return
    decision = _decide(role, path)
    if decision is None:
        return
    action, reason = decision
    if action == "allow":
        return
    common.emit_decision(action, reason)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 安全钩子异常一律 DENY
        common.fail_closed(f"H3 执行异常，按 DENY 处理：{exc}")
