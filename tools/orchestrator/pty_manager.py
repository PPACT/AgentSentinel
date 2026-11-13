"""单 PTY 常驻（Windows ConPTY）。

Phase 1：PowerShell shell；Phase 2：切换为交互式 claude TUI。
所有输入统一写入同一 PTY；输出以原始字节广播给 SSE 订阅者。
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from winpty import PtyProcess  # type: ignore[import-untyped]  # 无 stubs

from tools.orchestrator.launcher import assign_process, close_job, create_job

DEFAULT_DIMENSIONS = (30, 120)
READ_CHUNK = 8192
MAX_BACKLOG_BYTES = 512 * 1024
INPUT_MAX_CHARS = 16 * 1024
SSE_PING_SECONDS = 20.0
MAX_SESSION_RETRIES = 2
HEAD_LIMIT = 32 * 1024


def session_conflict_detected(data: bytes) -> bool:
    """底层引擎报告 session 被占用时，允许换新 session 重试。"""
    lowered = data.lower()
    if b"already in use" in lowered and b"session" in lowered:
        return True
    return b"session already exists" in lowered


def claude_command(session_id: str) -> list[str]:
    return ["claude", "--session-id", session_id]


class PtyManager:
    """单个持久 PTY；启动后 reader 线程持续收字节并广播。"""

    def __init__(
        self,
        *,
        cwd: str,
        command: tuple[str, ...] | None = None,
        session_id: str | None = None,
        command_factory: Callable[[str], list[str]] | None = None,
        env: dict[str, str] | None = None,
        dimensions: tuple[int, int] = DEFAULT_DIMENSIONS,
        state_path: str | os.PathLike[str] | None = None,
        role: str = "",
        agent_id: str = "",
        engine: str = "claude",
    ) -> None:
        self.cwd = cwd
        self.role = role.strip()
        self.agent_id = agent_id.strip()
        self.engine = engine.strip() or "claude"
        self._command_override = list(command) if command else None
        self._command_factory = command_factory or claude_command
        self._fixed_session = session_id.strip() if session_id and session_id.strip() else None
        self._command: list[str] = []
        self.session_id = self._fixed_session or ""
        self.env = env
        self.state_path = str(state_path) if state_path is not None else None
        self.rows, self.cols = dimensions
        self._lock = threading.Lock()
        self._process: PtyProcess | None = None
        self._job: int = 0
        self._reader: threading.Thread | None = None
        self._running = False
        self._exit_status: int | None = None
        self._pid = 0
        self._seq = 0
        self._retries = 0
        self._notice: dict[str, Any] | None = None
        self._notice_seq = 0
        self._backlog: deque[tuple[int, bytes]] = deque()
        self._backlog_bytes = 0
        self._subscribers: set[queue.Queue[tuple[str, dict[str, Any]]]] = set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "pid": self._pid,
            "role": self.role,
            "agent_id": self.agent_id,
            "engine": self.engine,
            "cols": self.cols,
            "rows": self.rows,
            "exit_status": self._exit_status,
            "backlog_bytes": self._backlog_bytes,
            "subscribers": len(self._subscribers),
            "retries": self._retries,
            "notice": self._notice,
            "command": self._command[0] if self._command else "claude",
            "session_id": self.session_id,
        }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._running and self._process is not None and self._process.isalive():
                return self._status_locked()
            self._cleanup_locked()
            self._retries = 0
            self._notice = None
            session = self._fixed_session or str(uuid.uuid4())
            self._spawn_locked(session)
            return self._status_locked()

    def _spawn_locked(self, session: str) -> None:
        command = self._command_override or self._command_factory(session)
        job = create_job()
        process: PtyProcess | None = None
        try:
            process = PtyProcess.spawn(
                command,
                cwd=self.cwd,
                env=self.env or os.environ.copy(),
                dimensions=(self.rows, self.cols),
            )
            if not assign_process(job, int(process.pid)):
                raise OSError("AssignProcessToJobObject failed")
        except Exception:
            if process is not None:
                process.terminate(force=True)
            close_job(job)
            raise
        self._process = process
        self._job = job
        self._pid = int(process.pid)
        self._command = command
        self.session_id = session
        self._running = True
        self._exit_status = None
        self._write_state_locked()
        self._reader = threading.Thread(target=self._read_loop, args=(process,), daemon=True)
        self._reader.start()

    def ensure_started(self) -> dict[str, Any]:
        with self._lock:
            running = self._running
        return self.status() if running else self.start()

    def write(self, data: str) -> bool:
        if not data:
            return True
        if len(data) > INPUT_MAX_CHARS:
            raise ValueError("input too large")
        with self._lock:
            process = self._process
            if process is None or not self._running:
                return False
        try:
            process.write(data)
        except (OSError, RuntimeError, EOFError):
            return False
        return True

    def set_session(self, session_id: str) -> None:
        """/clear 后底层换 session 时，更新旁路解析使用的 session id。"""
        session = session_id.strip()
        if not session:
            return
        with self._lock:
            self.session_id = session
            if self._running:
                self._write_state_locked()

    def resize(self, cols: int, rows: int) -> dict[str, Any]:
        cols = max(20, min(400, int(cols)))
        rows = max(5, min(200, int(rows)))
        with self._lock:
            process = self._process
            self.cols, self.rows = cols, rows
        if process is not None and process.isalive():
            try:
                process.setwinsize(rows, cols)
            except (OSError, RuntimeError):
                pass
        return self.status()

    def subscribe(self) -> tuple[queue.Queue[tuple[str, dict[str, Any]]], list[tuple[int, bytes]]]:
        backlog: list[tuple[int, bytes]] = []
        with self._lock:
            queue_: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
            self._subscribers.add(queue_)
            backlog = list(self._backlog)
        return queue_, backlog

    def unsubscribe(self, queue_: queue.Queue[tuple[str, dict[str, Any]]]) -> None:
        with self._lock:
            self._subscribers.discard(queue_)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
        if process is not None:
            try:
                process.terminate(force=True)
            except (OSError, RuntimeError):
                pass
            deadline = time.monotonic() + 5
            while process.isalive() and time.monotonic() < deadline:
                time.sleep(0.05)
        with self._lock:
            self._cleanup_locked()
        return self.status()

    def ping_seconds(self) -> float:
        return SSE_PING_SECONDS

    def _cleanup_locked(self) -> None:
        if self._job:
            try:
                close_job(self._job)
            except (OSError, RuntimeError):
                pass
        self._job = 0
        self._process = None
        self._running = False
        self._pid = 0
        self._clear_state()

    def _write_state_locked(self) -> None:
        """落盘 PTY pid/session 供兜底脚本定位残留进程。"""
        if not self.state_path:
            return
        payload = {
            "pid": self._pid,
            "role": self.role,
            "agent_id": self.agent_id,
            "engine": self.engine,
            "session_id": self.session_id,
            "command": self._command[0] if self._command else "",
        }
        try:
            temp = f"{self.state_path}.tmp"
            with open(temp, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
            os.replace(temp, self.state_path)
        except OSError:
            pass

    def _clear_state(self) -> None:
        if not self.state_path:
            return
        try:
            os.unlink(self.state_path)
        except OSError:
            pass

    def _read_loop(self, process: PtyProcess) -> None:
        head = bytearray()
        while True:
            try:
                chunk = process.read(READ_CHUNK)
            except (EOFError, OSError):
                break
            if not chunk:
                break
            data = chunk.encode("utf-8", errors="replace")
            if len(head) < HEAD_LIMIT:
                head.extend(data[: HEAD_LIMIT - len(head)])
            self._broadcast(data)
        exit_status = process.exitstatus
        retried = False
        with self._lock:
            if self._process is not process:
                return
            conflict = session_conflict_detected(bytes(head))
            if conflict and self._retries < MAX_SESSION_RETRIES:
                self._retries += 1
                self._cleanup_locked()
                new_session = str(uuid.uuid4())
                try:
                    self._spawn_locked(new_session)
                except (OSError, RuntimeError):
                    self._running = False
                    self._exit_status = exit_status
                    self._clear_state()
                    self._set_notice_locked(
                        "session_conflict",
                        "旧会话正被外部占用，自动开启新会话失败，请重试",
                        new_session,
                    )
                else:
                    retried = True
                    self._set_notice_locked(
                        "session_conflict",
                        "旧会话正被外部占用，已为您开启全新干净会话",
                        new_session,
                    )
            else:
                self._running = False
                self._exit_status = exit_status
                self._clear_state()
        if not retried:
            self._broadcast_exit(exit_status)

    def _set_notice_locked(self, kind: str, message: str, session: str) -> None:
        self._notice_seq += 1
        notice = {
            "seq": self._notice_seq,
            "kind": kind,
            "message": message,
            "session_id": session,
        }
        self._notice = notice
        for queue_ in self._subscribers:
            queue_.put(("notice", dict(notice)))

    def latest_notice(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._notice) if self._notice else None

    def _broadcast(self, data: bytes) -> None:
        with self._lock:
            self._seq += 1
            seq = self._seq
            self._backlog.append((seq, data))
            self._backlog_bytes += len(data)
            while self._backlog_bytes > MAX_BACKLOG_BYTES and self._backlog:
                _old_seq, old = self._backlog.popleft()
                self._backlog_bytes -= len(old)
            subscribers = list(self._subscribers)
        for queue_ in subscribers:
            queue_.put(("output", {"seq": seq, "data": data}))

    def _broadcast_exit(self, status: int | None) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for queue_ in subscribers:
            queue_.put(("exit", {"exit_status": status}))
