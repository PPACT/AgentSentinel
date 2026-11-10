"""Stop Hook：把外部会话最后一轮助手输出上报到编排 daemon 的 logs/<role>.log。

只在本进程带 `SENTINEL_ROLE` 时工作；daemon 未启动、token 缺失或网络失败时静默退出，
绝不阻塞 Claude Code。上报内容为单行、截断后的助手文本，Console 以 `[role]` 前缀展示。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from common import project_root, read_payload

MAX_CHARS = 1200
REQUEST_TIMEOUT = 1.5


def orchestrator_home() -> Path:
    override = os.environ.get("SENTINEL_ORCH_HOME", "").strip()
    return Path(override).resolve() if override else project_root() / ".claude" / "orchestrator"


def _text_blocks(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def last_assistant_text(payload: dict[str, Any]) -> str:
    """只使用 Stop 事件自带的 last_assistant_message；不读取项目外 transcript。"""
    direct = payload.get("last_assistant_message")
    if isinstance(direct, str):
        return direct
    if isinstance(direct, dict):
        return _text_blocks(direct.get("content"))
    return ""


def normalize_line(text: str, limit: int = MAX_CHARS) -> str:
    return " ".join(text.split())[:limit]


def report(role: str, line: str) -> bool:
    home = orchestrator_home()
    try:
        info = json.loads((home / "daemon.json").read_text(encoding="utf-8"))
        token = (home / "token").read_text(encoding="utf-8").strip()
        host = str(info["host"])
        port = int(info["port"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False
    body = json.dumps({"role": role, "line": line}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"http://{host}:{port}/logs/report",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return 200 <= int(response.status) < 300
    except OSError:
        return False


def main() -> int:
    role = os.environ.get("SENTINEL_ROLE", "").strip()
    if not role:
        return 0
    try:
        payload = read_payload()
    except (TypeError, ValueError):
        return 0
    line = normalize_line(last_assistant_text(payload))
    if line:
        report(role, f"[turn] {line}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001 - Hook 绝不让会话失败
        raise SystemExit(0)
