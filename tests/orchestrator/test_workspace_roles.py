"""动态角色：per-role command、本地注册、唤醒词首次注入与终端路径。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from tools.orchestrator import service
from tools.orchestrator.registry import AgentRecord, utc_now
from tools.orchestrator.workspaces import WorkspaceStore


def _daemon(tmp_path: Path) -> service.Daemon:
    return service.Daemon(tmp_path / "orch")


def _active_id(daemon: service.Daemon) -> int:
    workspace = daemon.active_workspace()
    return int(workspace.get("id") or 0)


def _save_role(
    daemon: service.Daemon,
    role: str,
    *,
    command: str = "",
    wake: str = "",
) -> None:
    daemon.workspaces.save_agent(
        {
            "workspace_id": _active_id(daemon),
            "role": role,
            "wake_prompt_type": "text",
            "wake_prompt_content": wake,
            "command": command,
        }
    )


def test_role_command_roundtrip(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "ws.sqlite3")
    workspace = store.seed_default(str(tmp_path))
    saved = store.save_agent(
        {
            "workspace_id": workspace["id"],
            "role": "reviewer",
            "wake_prompt_type": "text",
            "wake_prompt_content": "review",
            "command": "claude --model sonnet",
        }
    )
    assert saved["command"] == "claude --model sonnet"
    loaded = store.get_agent(int(workspace["id"]), "reviewer")
    assert loaded is not None
    assert loaded["command"] == "claude --model sonnet"


def test_configured_role_is_registered_as_local(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _save_role(daemon, "reviewer", wake="review")

    daemon._register_workspace_agents()
    record = daemon.registry.get("reviewer")

    assert record is not None
    assert record.pid == os.getpid()
    assert record.job_id == ""
    assert record.command == "workspace"
    first_agent_id = service._agent_id(record)

    daemon._register_workspace_agents()
    again = daemon.registry.get("reviewer")
    assert again is not None
    assert again.started_at == record.started_at
    assert service._agent_id(again) == first_agent_id


def test_external_alive_agent_is_not_overwritten(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _save_role(daemon, "reviewer", wake="review")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        now = utc_now()
        daemon.registry.upsert(
            AgentRecord(
                role="reviewer",
                pid=child.pid,
                pgid=child.pid,
                job_id="",
                status="registered",
                started_at=now,
                last_heartbeat=now,
                command="mcp_server session= ext",
                updated_at=now,
                project=daemon.project,
            )
        )
        daemon._register_workspace_agents()
        record = daemon.registry.get("reviewer")
        assert record is not None
        assert record.pid == child.pid
        assert record.command.startswith("mcp_server")
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_deleting_role_stops_local_record(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _save_role(daemon, "reviewer", wake="review")
    daemon._register_workspace_agents()
    assert daemon.registry.get("reviewer") is not None

    daemon.workspaces.delete_agent(_active_id(daemon), "reviewer")
    daemon._register_workspace_agents()
    record = daemon.registry.get("reviewer")
    assert record is not None
    assert record.status == "stopped"


def test_effective_command_prefers_role_command(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    _save_role(daemon, "reviewer", command="powershell -NoLogo -NoProfile", wake="")
    workspace = daemon.active_workspace()

    assert daemon._effective_command(workspace, "reviewer") == "powershell -NoLogo -NoProfile"
    assert daemon._effective_command(workspace, "dev") == str(workspace.get("command") or "claude")
    check = daemon.pty_preflight("reviewer")
    assert check["ok"] is True
    assert check["executable"].lower().startswith("powershell")


class _FakePty:
    def __init__(self) -> None:
        self.role = "reviewer"
        self.agent_id = "a1"
        self.session_id = "session-x"
        self.writes: list[str] = []
        self.running = True

    def status(self) -> dict[str, Any]:
        return {"running": self.running, "session_id": self.session_id, "role": self.role}

    def write(self, data: str) -> bool:
        self.writes.append(data)
        return True

    def stop(self) -> dict[str, Any]:
        self.running = False
        return self.status()


def test_wake_prompt_injected_on_first_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_WAKE_INJECT_DELAY_SECONDS", "0")
    monkeypatch.setattr(service, "read_history", lambda *args, **kwargs: None)
    injected: list[str] = []
    monkeypatch.setattr(service, "_inject_prompt", lambda pty, text: injected.append(text))

    daemon = _daemon(tmp_path)
    _save_role(daemon, "reviewer", wake="请担任 reviewer")
    fake = _FakePty()
    daemon.terminals.install(fake)  # type: ignore[arg-type]

    daemon._maybe_inject_wake("reviewer", "session-x", fake)  # type: ignore[arg-type]
    deadline = time.monotonic() + 3
    while not injected and time.monotonic() < deadline:
        time.sleep(0.05)

    assert injected == ["请担任 reviewer"]


def test_wake_prompt_skipped_when_history_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_WAKE_INJECT_DELAY_SECONDS", "0")
    monkeypatch.setattr(
        service,
        "read_history",
        lambda *args, **kwargs: {"messages": [{"role": "user"}]},
    )
    injected: list[str] = []
    monkeypatch.setattr(service, "_inject_prompt", lambda pty, text: injected.append(text))

    daemon = _daemon(tmp_path)
    _save_role(daemon, "reviewer", wake="请担任 reviewer")
    fake = _FakePty()
    daemon.terminals.install(fake)  # type: ignore[arg-type]

    daemon._maybe_inject_wake("reviewer", "session-x", fake)  # type: ignore[arg-type]
    time.sleep(0.3)
    assert injected == []


def test_terminal_path_normalization(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    server = service._Server(("127.0.0.1", 0), service._Handler)
    server.daemon = daemon
    try:
        handler = object.__new__(service._Handler)
        handler.server = server

        assert handler._normalize_terminal_path("/pty/1/input") == "/pty/input"
        assert handler._normalize_terminal_path("/pty/1/chat/stream") == "/pty/chat/stream"
        assert handler._normalize_terminal_path("/pty/stream") == "/pty/stream"
        assert handler._normalize_terminal_path("/pty/chat/stream") == "/pty/chat/stream"
        assert handler._normalize_terminal_path("/pty/9/input") is None
    finally:
        server.server_close()


def test_status_exposes_active_terminal_id(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    assert daemon.status()["active_terminal_id"] == "1"
    assert daemon.terminals.get("1") is daemon.terminals.active()
    assert daemon.terminals.get("2") is None
