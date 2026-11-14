"""PTY 与 Agent 唯一标识绑定：离线即回收，旧 agent_id 不得误杀新 Agent。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from tools.orchestrator import service
from tools.orchestrator.registry import AgentRecord, utc_now

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="ConPTY on Windows")

SHELL = "powershell.exe -NoLogo -NoProfile"


def _record(role: str, pid: int, status: str = "registered", job_id: str = "") -> AgentRecord:
    now = utc_now()
    return AgentRecord(
        role=role,
        pid=pid,
        pgid=pid,
        job_id=job_id,
        status=status,
        started_at=now,
        last_heartbeat=now,
        command="test",
        updated_at=now,
    )


def _register(daemon: service.Daemon, record: AgentRecord) -> str:
    daemon.registry.upsert(record)
    return service._agent_id(record)


def test_role_offline_reclaims_bound_pty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_PTY_COMMAND", SHELL)
    daemon = service.Daemon(tmp_path / "orch")
    agent_id = _register(daemon, _record("dev", os.getpid()))

    pty = daemon.pty_for("dev", agent_id)
    status = pty.start()
    assert status["running"] is True
    assert status["role"] == "dev"
    assert status["agent_id"] == agent_id
    assert (tmp_path / "orch" / "pty.json").exists()
    assert daemon.enforce_pty_binding() is False

    daemon.registry.set_status("dev", "stopped")
    assert daemon.enforce_pty_binding() is True
    assert daemon.terminals.active().status()["running"] is False
    assert not (tmp_path / "orch" / "pty.json").exists()


def test_role_switch_reclaims_and_resumes_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_PTY_COMMAND", SHELL)
    daemon = service.Daemon(tmp_path / "orch")
    dev_id = _register(daemon, _record("dev", os.getpid()))
    doc_id = _register(daemon, _record("doc", os.getpid()))

    dev_pty = daemon.pty_for("dev", dev_id)
    dev_pty.start()
    dev_session = dev_pty.session_id

    doc_pty = daemon.pty_for("doc", doc_id)
    assert doc_pty is not dev_pty
    assert doc_pty.role == "doc"
    assert doc_pty.agent_id == doc_id
    assert dev_pty.status()["running"] is False
    doc_pty.stop()

    resumed = daemon.pty_for("dev", dev_id)
    assert resumed.role == "dev"
    assert resumed.session_id == dev_session
    resumed.stop()


def test_role_switch_back_builds_resume_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """切回角色必须用 --resume；--session-id 会触发 session already exists。"""
    monkeypatch.delenv("SENTINEL_PTY_COMMAND", raising=False)
    daemon = service.Daemon(tmp_path / "orch")
    dev_id = _register(daemon, _record("dev", os.getpid()))
    doc_id = _register(daemon, _record("doc", os.getpid()))
    monkeypatch.setattr(
        service,
        "transcript_path",
        lambda project, session: tmp_path / "transcript.jsonl",
    )

    dev_pty = daemon.pty_for("dev", dev_id)
    dev_session = dev_pty.session_id
    daemon.pty_for("doc", doc_id)
    resumed = daemon.pty_for("dev", dev_id)

    assert resumed.session_id == dev_session
    assert resumed._command_factory(dev_session) == ["claude", "--resume", dev_session]
    fresh = daemon._new_pty("doc", "session-fresh", doc_id)
    assert fresh._command_factory("session-fresh") == [
        "claude",
        "--session-id",
        "session-fresh",
    ]


def test_role_sessions_persist_across_manager_restart(tmp_path: Path) -> None:
    path = tmp_path / "pty_sessions.json"
    first = service.TerminalManager(lambda *_: MagicMock(), sessions_path=path)
    first.remember_session("dev", "session-1")
    assert path.exists()

    restarted = service.TerminalManager(lambda *_: MagicMock(), sessions_path=path)
    assert restarted.session_for("dev") == "session-1"


def test_stale_agent_id_cannot_stop_current_pty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_PTY_COMMAND", SHELL)
    daemon = service.Daemon(tmp_path / "orch")
    agent_id = _register(daemon, _record("dev", os.getpid()))

    pty = daemon.pty_for("dev", agent_id)
    pty.start()
    assert daemon.stop_pty_for_role("dev", "deadbeefdeadbeef") is False
    assert daemon.terminals.active().status()["running"] is True
    assert daemon.stop_pty_for_role("dev", agent_id) is True
    assert daemon.terminals.active().status()["running"] is False


def test_re_registered_role_reclaims_old_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_PTY_COMMAND", SHELL)
    daemon = service.Daemon(tmp_path / "orch")
    first_id = _register(daemon, _record("dev", os.getpid()))
    pty = daemon.pty_for("dev", first_id)
    pty.start()

    second_id = _register(daemon, _record("dev", os.getppid()))
    assert second_id != first_id
    assert daemon.enforce_pty_binding() is True
    assert daemon.terminals.active().status()["running"] is False
