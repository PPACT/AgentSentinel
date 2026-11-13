"""Agent 引擎适配器：命令构造、能力声明与启动前环境预检。

中控不保存任何厂商专属路径；厂商差异只存在于适配器实现里。
"""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EngineAdapter:
    id: str
    label: str
    default_command: str
    supports_chat: bool
    supports_usage: bool
    supports_clear: bool
    session_flag: str = ""

    def build_command(self, command: str, session_id: str) -> list[str]:
        parts = split_command(command) or split_command(self.default_command)
        if self.session_flag and session_id:
            parts.extend([self.session_flag, session_id])
        return parts

    def capabilities(self) -> dict[str, bool]:
        return {
            "chat": self.supports_chat,
            "usage": self.supports_usage,
            "clear": self.supports_clear,
        }


ENGINES: dict[str, EngineAdapter] = {
    "claude": EngineAdapter(
        id="claude",
        label="Claude Code",
        default_command="claude",
        supports_chat=True,
        supports_usage=True,
        supports_clear=True,
        session_flag="--session-id",
    ),
    "codex": EngineAdapter(
        id="codex",
        label="Codex CLI",
        default_command="codex",
        supports_chat=False,
        supports_usage=False,
        supports_clear=False,
    ),
    "generic": EngineAdapter(
        id="generic",
        label="通用 CLI",
        default_command="",
        supports_chat=False,
        supports_usage=False,
        supports_clear=False,
    ),
}


def adapter_for(engine: str) -> EngineAdapter:
    return ENGINES.get((engine or "").strip().lower(), ENGINES["generic"])


def split_command(command: str) -> list[str]:
    """按 Windows 命令行习惯拆分；保留路径反斜杠。"""
    text = command.strip()
    if not text:
        return []
    try:
        parts = shlex.split(text, posix=os.name != "nt")
    except ValueError:
        parts = text.split()
    if os.name == "nt":
        parts = [
            part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'" else part
            for part in parts
        ]
    return parts


def resolve_command(command: str) -> str | None:
    parts = split_command(command)
    if not parts:
        return None
    executable = parts[0]
    candidate = Path(executable).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    return shutil.which(executable, path=os.environ.get("PATH", ""))


def preflight(command: str, project_path: str) -> dict[str, Any]:
    """启动前预检：命令可执行文件 + 项目目录。不拉起任何进程。"""
    parts = split_command(command)
    if not parts:
        return {"ok": False, "reason": "command_required", "error": "启动命令不能为空"}
    resolved = resolve_command(command)
    if resolved is None:
        return {
            "ok": False,
            "reason": "command_not_found",
            "error": f"未检测到 {parts[0]} 命令，请先配置好环境",
            "executable": parts[0],
        }
    path_text = project_path.strip()
    if not path_text:
        return {"ok": False, "reason": "path_required", "error": "项目路径不能为空"}
    if not Path(path_text).expanduser().is_dir():
        return {
            "ok": False,
            "reason": "path_not_found",
            "error": f"项目路径不存在：{path_text}",
        }
    return {
        "ok": True,
        "command_path": resolved,
        "executable": parts[0],
        "project_path": str(Path(path_text).expanduser().resolve()),
    }
