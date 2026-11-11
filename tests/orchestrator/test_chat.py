"""交互式聊天 PoC：命令分支、事件归集、日志脱敏与 Job 回收。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from tools.orchestrator.chat import ChatManager, chat_command

from agentsentinel.contracts import ScanVerdict
from agentsentinel.governance.sanitizer import ScanResult


class _FakeProcess:
    def __init__(self, lines: list[str], pid: int = 4321) -> None:
        self.pid = pid
        self.stdout = io.StringIO("\n".join(lines) + "\n")
        self.killed = False
        self.waited = False

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0

    def kill(self) -> None:
        self.killed = True


def _events(result: str = "你好") -> list[str]:
    return [
        json.dumps({"type": "system", "subtype": "init", "session_id": "s"}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "想一下"},
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}},
                        {"type": "text", "text": result},
                    ]
                },
            }
        ),
        json.dumps({"type": "result", "subtype": "success", "result": result}),
    ]


def test_chat_command_uses_session_id_then_resume() -> None:
    first = chat_command("hi", "1111-2222", resume=False)
    second = chat_command("again", "1111-2222", resume=True)

    assert first[first.index("--session-id") + 1] == "1111-2222"
    assert "--resume" not in first
    assert second[second.index("--resume") + 1] == "1111-2222"
    assert "--session-id" not in second
    assert "--output-format" in first and "stream-json" in first
    assert "--verbose" in first


def _manager(tmp_path: Path, **kwargs: Any) -> tuple[ChatManager, list[list[str]], list[int]]:
    commands: list[list[str]] = []
    jobs: list[int] = []

    def spawn(command: list[str], _environment: dict[str, str]) -> _FakeProcess:
        commands.append(command)
        return _FakeProcess(_events())

    def assign(job: int, _pid: int) -> bool:
        jobs.append(job)
        return True

    manager = ChatManager(
        tmp_path / "dev-log.md",
        spawn=spawn,
        job_factory=lambda: 7,
        assign=assign,
        close=lambda _job: None,
        timeout_seconds=5,
        **kwargs,
    )
    return manager, commands, jobs


def test_stream_turn_collects_result_and_closes_job(tmp_path: Path) -> None:
    manager, commands, jobs = _manager(tmp_path)
    emitted: list[tuple[str, str]] = []

    def emit(event: str, data: str) -> bool:
        emitted.append((event, data))
        return True

    summary = manager.stream_turn("你好", None, emit)

    assert summary["result"] == "你好"
    assert summary["resumed"] is False
    assert summary["session_id"]
    assert jobs == [7]
    assert len(commands) == 1
    assert len(emitted) == 3
    log = (tmp_path / "dev-log.md").read_text(encoding="utf-8")
    assert "用户要求" in log and "你好" in log
    assert summary["session_id"] in log


def test_stream_turn_resumes_existing_session(tmp_path: Path) -> None:
    manager, commands, _jobs = _manager(tmp_path)
    session = "aaaa-bbbb-cccc"

    summary = manager.stream_turn("继续", session, lambda _event, _data: True)

    assert summary["resumed"] is True
    assert summary["session_id"] == session
    assert "--resume" in commands[0]
    assert session in commands[0]


class _DenySensitiveSanitizer:
    def scan(self, content: str) -> ScanResult:
        if "敏感" in content:
            return ScanResult(verdict=ScanVerdict.DENY, reason="custom:password (line 1)")
        return ScanResult(verdict=ScanVerdict.ALLOW)


def test_dev_log_redacts_denied_content(tmp_path: Path) -> None:
    manager = ChatManager(tmp_path / "dev-log.md", sanitizer=_DenySensitiveSanitizer())

    manager._append_dev_log("一段被判定为敏感的输入", "结果正常", "sid")

    log = (tmp_path / "dev-log.md").read_text(encoding="utf-8")
    assert "一段被判定为敏感的输入" not in log
    assert "[已脱敏: custom:password" in log
    assert "结果正常" in log


def test_client_disconnect_kills_process(tmp_path: Path) -> None:
    processes: list[_FakeProcess] = []

    def spawn(_command: list[str], _environment: dict[str, str]) -> _FakeProcess:
        process = _FakeProcess(_events())
        processes.append(process)
        return process

    manager = ChatManager(
        tmp_path / "dev-log.md",
        spawn=spawn,
        job_factory=lambda: 7,
        assign=lambda _job, _pid: True,
        close=lambda _job: None,
        timeout_seconds=5,
    )

    summary = manager.stream_turn("你好", None, lambda _event, _data: False)

    assert summary["disconnected"] is True
    assert processes[0].killed is True
