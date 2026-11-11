"""最小交互式聊天：每轮一个 ``claude -p``，首轮 ``--session-id``、后续 ``--resume``。

设计约束（2026-11-11 用户拍板）：
- 路线一：每次带 session id 重启进程，不做常驻 stdin。
- 中控全权负责生命周期：进程加入 Job Object，daemon 退出/超时即回收。
- 开发日志落盘前按 ``RegexSanitizer`` 逐段脱敏，DENY 的片段只留规则名。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from agentsentinel.governance.sanitizer import RegexSanitizer, Sanitizer
from tools.orchestrator.launcher import (
    assign_process,
    close_job,
    create_job,
    hidden_creationflags,
    worker_environment,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = PROJECT_ROOT / "docs" / "local" / "开发日志.md"
LOG_PATH_ENV = "SENTINEL_DEV_LOG_PATH"
BUDGET_ENV = "SENTINEL_CHAT_BUDGET_USD"
DEFAULT_BUDGET_USD = "0.50"
TIMEOUT_ENV = "SENTINEL_CHAT_TIMEOUT_SECONDS"
DEFAULT_TIMEOUT_SECONDS = 300.0
ALLOWED_TOOLS = "Read,Glob,Grep"
PROMPT_MAX_CHARS = 8000
SESSION_PATTERN = r"^[0-9a-fA-F-]{8,64}$"


class ChatProcess(Protocol):
    """subprocess.Popen 的最小子集；测试可注入假进程。"""

    pid: int
    stdout: Any

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


def dev_log_path() -> Path:
    override = os.environ.get(LOG_PATH_ENV, "").strip()
    return Path(override) if override else DEFAULT_LOG_PATH


def chat_timeout_seconds() -> float:
    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return DEFAULT_TIMEOUT_SECONDS
        if value > 0:
            return value
    return DEFAULT_TIMEOUT_SECONDS


def chat_budget_usd() -> str:
    return os.environ.get(BUDGET_ENV, "").strip() or DEFAULT_BUDGET_USD


def chat_command(prompt: str, session_id: str, *, resume: bool) -> list[str]:
    """首轮用 ``--session-id`` 建会话，后续 ``--resume`` 续接同一会话。"""
    command = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-budget-usd",
        chat_budget_usd(),
        "--allowedTools",
        ALLOWED_TOOLS,
    ]
    if resume:
        command.extend(["--resume", session_id])
    else:
        command.extend(["--session-id", session_id])
    return command


def chat_environment() -> dict[str, str]:
    environment = worker_environment("chat", real=True)
    # 不让子会话里的 MCP 以 role=chat 自动注册，避免污染 /status。
    environment.pop("SENTINEL_ROLE", None)
    environment.pop("CLAUDE_CODE_SESSION_ID", None)
    return environment


def _default_spawn(command: list[str], environment: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=hidden_creationflags(),
    )


def _assistant_text(event: dict[str, Any]) -> list[str]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    ]


class ChatManager:
    """按 session 串行的一轮对话；同时最多一个活跃进程。"""

    def __init__(
        self,
        log_path: Path | None = None,
        *,
        sanitizer: Sanitizer | None = None,
        spawn: Callable[[list[str], dict[str, str]], ChatProcess] | None = None,
        job_factory: Callable[[], int] = create_job,
        assign: Callable[[int, int], bool] = assign_process,
        close: Callable[[int], None] = close_job,
        timeout_seconds: float | None = None,
    ) -> None:
        self.log_path = log_path or dev_log_path()
        self._sanitizer = sanitizer or RegexSanitizer()
        self._spawn = spawn or _default_spawn
        self._job_factory = job_factory
        self._assign = assign
        self._close = close
        self._timeout = timeout_seconds if timeout_seconds is not None else chat_timeout_seconds()
        self._lock = threading.Lock()
        self._active: dict[str, ChatProcess] = {}

    def is_busy(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        with self._lock:
            return session_id in self._active

    def stop(self, session_id: str) -> bool:
        with self._lock:
            process = self._active.get(session_id)
        if process is None:
            return False
        try:
            process.kill()
        except OSError:
            return False
        return True

    def stop_all(self) -> None:
        with self._lock:
            processes = list(self._active.values())
        for process in processes:
            try:
                process.kill()
            except OSError:
                continue

    def stream_turn(
        self,
        prompt: str,
        session_id: str | None,
        emit: Callable[[str, str], bool],
    ) -> dict[str, Any]:
        """流式跑完一轮；emit 返回 False 表示客户端断开，进程将被终止。"""
        sid = session_id or str(uuid.uuid4())
        resume = bool(session_id)
        command = chat_command(prompt, sid, resume=resume)
        job = self._job_factory()
        texts: list[str] = []
        result_event: dict[str, Any] | None = None
        disconnected = False
        error = ""
        try:
            process = self._spawn(command, chat_environment())
            with self._lock:
                self._active[sid] = process
            if not self._assign(job, process.pid):
                raise OSError("AssignProcessToJobObject failed")
            watchdog = threading.Timer(self._timeout, process.kill)
            watchdog.daemon = True
            watchdog.start()
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    raw = line.rstrip("\n")
                    if not raw:
                        continue
                    event: dict[str, Any] | None = None
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        parsed = None
                    if isinstance(parsed, dict):
                        event = parsed
                        if event.get("type") == "assistant":
                            texts.extend(_assistant_text(event))
                        elif event.get("type") == "result":
                            result_event = event
                    if not emit("message", raw):
                        disconnected = True
                        try:
                            process.kill()
                        except OSError:
                            pass
                        break
                process.wait(timeout=10)
            finally:
                watchdog.cancel()
        except OSError as exc:
            error = str(exc)
        finally:
            with self._lock:
                self._active.pop(sid, None)
            try:
                self._close(job)
            except OSError:
                pass

        result_text = ""
        if result_event is not None:
            result_text = str(result_event.get("result", "") or "")
        if not result_text and texts:
            result_text = texts[-1]
        cost = result_event.get("total_cost_usd") if result_event else None
        self._append_dev_log(prompt, result_text, sid, error=error, disconnected=disconnected)
        return {
            "session_id": sid,
            "resumed": resume,
            "result": result_text,
            "cost_usd": cost,
            "disconnected": disconnected,
            "error": error,
            "closed_by": "client" if disconnected else "process",
        }

    def _append_dev_log(
        self,
        prompt: str,
        result: str,
        session_id: str,
        *,
        error: str = "",
        disconnected: bool = False,
    ) -> None:
        prompt_text = self._redact(prompt)
        result_text = self._redact(result or error or ("[客户端断开]" if disconnected else ""))
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = (
            f"### [{stamp}] 角色: chat\n\n"
            f"**🗣️ 用户要求**: {prompt_text}\n\n"
            f"**📦 执行结果**: {result_text}\n\n"
            f"**📄 会话**: {session_id}\n\n"
        )
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(entry)
        except OSError:
            return

    def _redact(self, text: str) -> str:
        if not text:
            return ""
        verdict = self._sanitizer.scan(text)
        if verdict.allowed:
            return text
        return f"[已脱敏: {verdict.reason}]"
