"""Stop Hook：外部会话领取投递给本角色的任务，用 block 让当前 turn 继续执行。

角色来源：`SENTINEL_ROLE` 优先；否则读本会话的 `agents/<session_id>.json` 缓存
（`--resume` 复用同一 session id 时仍可恢复）。daemon 离线或无任务时静默退出。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from common import project_root, read_payload

REQUEST_TIMEOUT = 1.5
_SAFE_CACHE_KEY = re.compile(r"[A-Za-z0-9._-]{1,128}")


def orchestrator_home() -> Path:
    override = os.environ.get("SENTINEL_ORCH_HOME", "").strip()
    return Path(override).resolve() if override else project_root() / ".claude" / "orchestrator"


def _cache_key(value: str) -> str:
    if _SAFE_CACHE_KEY.fullmatch(value):
        return value
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _session_id(payload: dict[str, Any]) -> str:
    raw = payload.get("session_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()


def _role(payload: dict[str, Any]) -> str:
    env_role = os.environ.get("SENTINEL_ROLE", "").strip()
    if env_role:
        return env_role
    session = _session_id(payload)
    if not session:
        return ""
    path = orchestrator_home() / "agents" / f"{_cache_key(session)}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict) or str(data.get("session_id", "")) != session:
        return ""
    role = data.get("role")
    return role.strip() if isinstance(role, str) else ""


def _pending_tasks(role: str) -> list[dict[str, Any]]:
    home = orchestrator_home()
    try:
        info = json.loads((home / "daemon.json").read_text(encoding="utf-8"))
        token = (home / "token").read_text(encoding="utf-8").strip()
        host = str(info["host"])
        port = int(info["port"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return []
    url = f"http://{host}:{port}/tasks?role={urllib.parse.quote(role)}&status=delivered"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            data = json.loads(response.read() or b"{}")
    except (OSError, json.JSONDecodeError):
        return []
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        return []
    return [
        task
        for task in tasks
        if isinstance(task, dict) and str(task.get("to_role", "")).casefold() == role.casefold()
    ]


def main() -> int:
    try:
        payload = read_payload()
    except (TypeError, ValueError):
        return 0
    if payload.get("stop_hook_active") is True:
        return 0
    role = _role(payload)
    if not role:
        return 0
    tasks = _pending_tasks(role)
    if not tasks:
        return 0
    first = tasks[0]
    task_id = str(first.get("task_id", ""))
    intent = str(first.get("intent", ""))
    pointer = str(first.get("pointer", ""))
    reason = (
        f"有新编排任务待领取：{task_id}（{intent}）。"
        f"请先调用 orchestrator_task(task_id={task_id}) 查看 pointer/acceptance，"
        "再调用 orchestrator_ack 领取并执行，完成后 orchestrator_completed。"
    )
    if pointer:
        reason += f" pointer={pointer}"
    if len(tasks) > 1:
        reason += f" 另有 {len(tasks) - 1} 个 delivered 任务。"
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001 - Hook 绝不让会话失败
        raise SystemExit(0)
