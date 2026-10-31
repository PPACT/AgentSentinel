"""H2：依赖与环境守卫（PreToolUse / Bash + Edit|Write|MultiEdit）。

``pip`` / ``conda`` / ``sudo`` 一律 DENY；``uv pip install`` 与写
``requirements.txt`` 走 ASK；核心框架版本改动 DENY。
"""

from __future__ import annotations

import re
from typing import Any

import common

_SUDO = re.compile(r"(?:^|[;&|]\s*|\s)sudo(?:\s|$)")
_UV_PIP_INSTALL = re.compile(r"\buv\s+pip\s+install\b")
_PIP_INSTALL = re.compile(r"\b(?:pip3?|python(?:3(?:\.\d+)?)?\s+-m\s+pip)\s+install\b")
_CONDA_INSTALL = re.compile(r"\bconda\s+install\b")

_CORE_PINS = ("agentscope[full]==2.0.8", "detect-secrets==1.5.0")
_AGENTSCOPE_PIN = re.compile(r"agentscope\[full\]==2\.0\.8")
_DETECT_SECRETS_PIN = re.compile(r"detect-secrets==1\.5\.0")


def _violates_core_versions(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lowered = stripped.lower()
        if "agentscope" in lowered and not _AGENTSCOPE_PIN.search(lowered):
            return True
        if "detect-secrets" in lowered and not _DETECT_SECRETS_PIN.search(lowered):
            return True
    return False


def _handle_bash(payload: dict[str, Any], command: str) -> None:
    if _SUDO.search(command):
        common.emit_decision("deny", "禁止使用 sudo 或系统级提权命令")
    if _UV_PIP_INSTALL.search(command):
        if common.payload_cwd(payload) != common.project_root():
            common.emit_decision("deny", "uv pip install 必须在项目根目录执行，确保命中 .venv")
        common.emit_decision("ask", "安装/变更依赖需用户当场确认（AGENTS.md §2）")
    if _PIP_INSTALL.search(command):
        common.emit_decision("deny", "禁止直接使用 pip；依赖只能通过 uv 在项目 .venv 中管理")
    if _CONDA_INSTALL.search(command):
        common.emit_decision("deny", "禁止使用 conda install")


def _handle_requirements_write(payload: dict[str, Any]) -> None:
    tool_in = common.tool_input(payload)
    texts = common.content_texts(tool_in)
    if any(_violates_core_versions(text) for text in texts):
        common.emit_decision(
            "deny", "禁止改动核心框架版本（agentscope[full]==2.0.8 / detect-secrets==1.5.0）"
        )

    old = tool_in.get("old_string")
    old_text = old if isinstance(old, str) else ""
    new_text = "\n".join(texts)
    for pin in _CORE_PINS:
        if pin in old_text and pin not in new_text:
            common.emit_decision("deny", f"禁止移除核心框架版本锁定：{pin}")

    common.emit_decision("ask", "requirements.txt 变更需用户当场确认（AGENTS.md §2）")


def main() -> None:
    payload = common.read_payload()
    tool_name = payload.get("tool_name")
    if tool_name == "Bash":
        command = common.tool_input(payload).get("command")
        if isinstance(command, str) and command:
            _handle_bash(payload, command)
        return
    if tool_name not in {"Edit", "Write", "MultiEdit"}:
        return
    file_path = common.tool_input(payload).get("file_path")
    if common.relative_project_path(file_path, payload) == "requirements.txt":
        _handle_requirements_write(payload)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 安全钩子异常一律 DENY
        common.fail_closed(f"H2 执行异常，按 DENY 处理：{exc}")
