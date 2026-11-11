"""交互式聊天 PoC：命令分支、事件归集、日志脱敏与 Job 回收。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from tools.orchestrator.chat import ChatBusy, ChatManager, chat_command

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


def _events(result: str = "你好", session_id: str = "s") -> list[str]:
    return [
        json.dumps({"type": "system", "subtype": "init", "session_id": session_id}),
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
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": result,
                "session_id": session_id,
            }
        ),
    ]


def _command_session(command: list[str]) -> str:
    for flag in ("--session-id", "--resume"):
        if flag in command:
            return command[command.index(flag) + 1]
    return "missing"


def test_chat_command_uses_session_id_then_resume() -> None:
    first = chat_command("hi", "1111-2222", resume=False)
    second = chat_command("again", "1111-2222", resume=True)
    fork = chat_command("takeover", "1111-2222", resume=True, fork=True)

    assert first[first.index("--session-id") + 1] == "1111-2222"
    assert "--resume" not in first
    assert second[second.index("--resume") + 1] == "1111-2222"
    assert "--session-id" not in second
    assert "--fork-session" not in second
    assert "--fork-session" in fork
    assert "--output-format" in first and "stream-json" in first
    assert "--verbose" in first


def _manager(tmp_path: Path, **kwargs: Any) -> tuple[ChatManager, list[list[str]], list[int]]:
    commands: list[list[str]] = []
    jobs: list[int] = []

    def spawn(command: list[str], _environment: dict[str, str]) -> _FakeProcess:
        commands.append(command)
        requested = _command_session(command)
        session = "forked-session" if "--fork-session" in command else requested
        return _FakeProcess(_events(session_id=session))

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

    summary = manager.stream_turn("dev", "你好", emit)

    assert summary["result"] == "你好"
    assert summary["role"] == "dev"
    assert summary["resumed"] is False
    assert summary["session_id"]
    assert jobs == [7]
    assert len(commands) == 1
    assert len(emitted) == 3
    log = (tmp_path / "dev-log.md").read_text(encoding="utf-8")
    assert "用户要求" in log and "你好" in log
    assert "角色: dev" in log
    assert summary["session_id"] in log


def test_stream_turn_resumes_existing_session(tmp_path: Path) -> None:
    manager, commands, _jobs = _manager(tmp_path)

    first = manager.stream_turn("dev", "第一轮", lambda _event, _data: True)
    second = manager.stream_turn("dev", "继续", lambda _event, _data: True)

    assert first["resumed"] is False
    assert second["resumed"] is True
    assert second["session_id"] == first["session_id"]
    assert "--session-id" in commands[0]
    assert "--resume" in commands[1]
    assert first["session_id"] in commands[1]


def test_roles_get_isolated_sessions(tmp_path: Path) -> None:
    manager, commands, _jobs = _manager(tmp_path)

    dev = manager.stream_turn("dev", "dev-1", lambda _event, _data: True)
    doc = manager.stream_turn("doc", "doc-1", lambda _event, _data: True)
    dev2 = manager.stream_turn("dev", "dev-2", lambda _event, _data: True)

    assert dev["session_id"] != doc["session_id"]
    assert dev2["session_id"] == dev["session_id"]
    assert doc["session_id"] in commands[1]
    assert dev["session_id"] in commands[2]


def test_first_turn_forks_external_session(tmp_path: Path) -> None:
    manager, commands, _jobs = _manager(
        tmp_path,
        external_session=lambda role: "external-session" if role == "dev" else None,
    )

    first = manager.stream_turn("dev", "继承终端上下文", lambda _event, _data: True)
    assert first["forked"] is True
    assert first["inherited_from"] == "external-session"
    assert first["session_id"] == "forked-session"
    assert "--resume" in commands[0] and "external-session" in commands[0]
    assert "--fork-session" in commands[0]

    second = manager.stream_turn("dev", "继续", lambda _event, _data: True)
    assert second["forked"] is False
    assert second["inherited_from"] is None
    assert second["session_id"] == "forked-session"
    assert "--fork-session" not in commands[1]
    assert "forked-session" in commands[1]

    doc = manager.stream_turn("doc", "新会话", lambda _event, _data: True)
    assert doc["forked"] is False
    assert "--session-id" in commands[2]


def test_same_role_busy_raises(tmp_path: Path) -> None:
    manager, _commands, _jobs = _manager(tmp_path)
    manager._inflight.add("dev")

    with pytest.raises(ChatBusy):
        manager.stream_turn("dev", "再来一轮", lambda _event, _data: True)


class _DenySensitiveSanitizer:
    def scan(self, content: str) -> ScanResult:
        if "敏感" in content:
            return ScanResult(verdict=ScanVerdict.DENY, reason="custom:password (line 1)")
        return ScanResult(verdict=ScanVerdict.ALLOW)


def test_dev_log_redacts_denied_content(tmp_path: Path) -> None:
    manager = ChatManager(tmp_path / "dev-log.md", sanitizer=_DenySensitiveSanitizer())

    manager._append_dev_log("dev", "一段被判定为敏感的输入", "结果正常", "sid")

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

    summary = manager.stream_turn("dev", "你好", lambda _event, _data: False)

    assert summary["disconnected"] is True
    assert processes[0].killed is True
