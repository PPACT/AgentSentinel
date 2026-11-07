"""scripts/wake.py：registry 查表、会话映射、存活投递与降级新建。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts import wake

REPO_ROOT = Path(__file__).resolve().parents[1]
WAKE_SCRIPT = REPO_ROOT / "scripts" / "wake.py"


def _run(
    project_dir: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    process_env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    process_env["CLAUDE_CONFIG_DIR"] = str(project_dir / ".claude-home")
    process_env["PYTHONUTF8"] = "1"
    process_env.pop("SENTINEL_ROLE", None)
    process_env.pop("SENTINEL_SESSION_ID", None)
    if env is not None:
        process_env.update(env)
    return subprocess.run(
        [sys.executable, str(WAKE_SCRIPT), *args],
        cwd=str(project_dir),
        env=process_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )


def _registry_file(project_dir: Path) -> Path:
    return project_dir / ".claude" / "mailbox" / "registry.json"


def _read_registry(project_dir: Path) -> dict[str, Any]:
    path = _registry_file(project_dir)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write_registry(
    project_dir: Path,
    *,
    roles: dict[str, Any] | None = None,
    sessions: dict[str, Any] | None = None,
) -> None:
    path = _registry_file(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-11-07T00:00:00Z",
                "roles": roles or {},
                "sessions": sessions or {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_session(
    config_dir: Path,
    *,
    pid: int,
    session_id: str,
    socket_path: str,
    name: str,
) -> None:
    sessions = config_dir / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    record = {
        "pid": pid,
        "sessionId": session_id,
        "messagingSocketPath": socket_path,
        "name": name,
        "cwd": str(config_dir),
    }
    (sessions / f"{pid}.json").write_text(json.dumps(record), encoding="utf-8")
    (sessions / f"{pid}.{'0' * 64}.key").write_text(
        json.dumps({"peerToken": "a" * 32}),
        encoding="utf-8",
    )


def _isolate(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
    *,
    env_role: str | None = None,
) -> Path:
    config_dir = project_dir / ".claude-home"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("SENTINEL_ROLE", raising=False)
    monkeypatch.delenv("SENTINEL_SESSION_ID", raising=False)
    if env_role is not None:
        monkeypatch.setenv("SENTINEL_ROLE", env_role)
    return config_dir


def _fake_deliver(
    calls: list[tuple[dict[str, Any], str]],
    result: tuple[bool, str] = (True, ""),
) -> Any:
    def deliver(record: dict[str, Any], text: str) -> tuple[bool, str]:
        calls.append((record, text))
        return result

    return deliver


def test_register_writes_single_registry_file(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "register",
        "--role",
        "doc",
        "--session",
        "agentsentinel-doc",
        "--ref",
        "abcd12",
        "--pid",
        "1234",
    )

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["registered"] is True
    data = _read_registry(tmp_path)
    assert data["version"] == 1
    assert data["roles"]["doc"]["session"] == "agentsentinel-doc"
    assert data["roles"]["doc"]["ref"] == "abcd12"
    assert data["roles"]["doc"]["pid"] == 1234
    assert data["roles"]["doc"]["updated_at"]
    assert data["sessions"] == {}
    assert not (tmp_path / ".claude" / "mailbox" / "roles").exists()


def test_register_upserts_without_conflict(tmp_path: Path) -> None:
    first = _run(tmp_path, "register", "--role", "dev", "--pid", "111")
    second = _run(tmp_path, "register", "--role", "dev", "--pid", "222")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    data = _read_registry(tmp_path)
    assert data["roles"]["dev"]["pid"] == 222


def test_resolve_reports_alive_and_missing(tmp_path: Path) -> None:
    registered = _run(
        tmp_path,
        "register",
        "--role",
        "doc",
        "--session",
        "doc-window",
        "--pid",
        str(os.getpid()),
    )
    assert registered.returncode == 0, registered.stderr

    result = _run(tmp_path, "resolve", "--role", "doc")

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["role"] == "doc"
    assert out["session"] == "doc-window"
    assert out["alive"] is True

    missing = _run(tmp_path, "resolve", "--role", "dev")
    assert missing.returncode == 1
    assert missing.stdout.strip() == "{}"


def test_pid_alive_current_and_invalid() -> None:
    assert wake.pid_alive(os.getpid()) is True
    assert wake.pid_alive(0) is False
    assert wake.pid_alive(-1) is False
    assert wake.pid_alive("not-a-pid") is False


def test_build_pipe_payload_has_auth_then_user_frame() -> None:
    payload = wake._build_pipe_payload("a" * 32, "hello")

    lines = payload.decode("utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"type": "auth", "token": "a" * 32}
    frame = json.loads(lines[1])
    assert frame["type"] == "user"
    assert frame["message"] == {"role": "user", "content": "hello"}


def test_corrupt_registry_degrades_to_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    path = _registry_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    data = wake.load_registry(tmp_path)

    assert data["roles"] == {}
    assert data["sessions"] == {}


def test_notify_non_target_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    spawned: list[str] = []
    monkeypatch.setattr(wake, "spawn_role_window", lambda root, role: spawned.append(role))

    result = wake.notify(tmp_path, file="docs/local/交流区/STATUS.md", session_id="sid-doc")

    assert result["action"] == "noop"
    assert result["reason"] == "non-target"
    assert spawned == []


def test_notify_unknown_sender_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    spawned: list[str] = []
    monkeypatch.setattr(wake, "spawn_role_window", lambda root, role: spawned.append(role))

    result = wake.notify(tmp_path, file=wake.TARGET_FILE, session_id="")

    assert result["action"] == "noop"
    assert result["reason"] == "unknown-sender"
    assert spawned == []


def test_notify_delivers_when_peer_alive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = _isolate(monkeypatch, tmp_path)
    _write_registry(
        tmp_path,
        roles={"dev": {"session": "dev", "ref": "sid-dev", "pid": 1234, "updated_at": "x"}},
        sessions={"sid-doc": "doc"},
    )
    _write_session(
        config_dir,
        pid=1234,
        session_id="sid-dev",
        socket_path=r"\\.\pipe\LOCAL\cc-msg-dev",
        name="dev",
    )
    calls: list[tuple[dict[str, Any], str]] = []
    monkeypatch.setattr(wake, "pid_alive", lambda pid: pid == 1234)
    monkeypatch.setattr(wake, "deliver_peer_message", _fake_deliver(calls))

    result = wake.notify(tmp_path, file=wake.TARGET_FILE, session_id="sid-doc")

    assert result["action"] == "delivered"
    assert result["role"] == "dev"
    assert len(calls) == 1
    assert "from=doc" in calls[0][1]
    data = _read_registry(tmp_path)
    assert data["sessions"]["sid-doc"] == "doc"


def test_notify_env_role_fallback_writes_session_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = _isolate(monkeypatch, tmp_path, env_role="doc")
    _write_registry(
        tmp_path,
        roles={"dev": {"session": "dev", "ref": "sid-dev", "pid": 1234, "updated_at": "x"}},
    )
    _write_session(
        config_dir,
        pid=1234,
        session_id="sid-dev",
        socket_path=r"\\.\pipe\LOCAL\cc-msg-dev",
        name="dev",
    )
    calls: list[tuple[dict[str, Any], str]] = []
    monkeypatch.setattr(wake, "pid_alive", lambda pid: pid == 1234)
    monkeypatch.setattr(wake, "deliver_peer_message", _fake_deliver(calls))

    result = wake.notify(tmp_path, file=wake.TARGET_FILE, session_id="sid-doc")

    assert result["action"] == "delivered"
    assert len(calls) == 1
    data = _read_registry(tmp_path)
    assert data["sessions"]["sid-doc"] == "doc"


def test_notify_missing_peer_spawns_and_writes_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _write_registry(tmp_path, sessions={"sid-doc": "doc"})
    spawned: list[str] = []

    def fake_spawn(root: Path, role: str) -> dict[str, Any] | None:
        spawned.append(role)
        return {"session": "new-dev", "ref": "sid-new-dev", "pid": 4321}

    monkeypatch.setattr(wake, "spawn_role_window", fake_spawn)

    result = wake.notify(tmp_path, file=wake.TARGET_FILE, session_id="sid-doc")

    assert result["action"] == "spawned"
    assert spawned == ["dev"]
    data = _read_registry(tmp_path)
    assert data["roles"]["dev"]["session"] == "new-dev"
    assert data["roles"]["dev"]["ref"] == "sid-new-dev"
    assert data["roles"]["dev"]["pid"] == 4321


def test_notify_dead_peer_pid_spawns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    _write_registry(
        tmp_path,
        roles={"dev": {"session": "stale", "ref": "old", "pid": 999999, "updated_at": "x"}},
        sessions={"sid-doc": "doc"},
    )
    spawned: list[str] = []

    def fake_spawn(root: Path, role: str) -> dict[str, Any] | None:
        spawned.append(role)
        return {"session": "fresh-dev", "ref": "sid-fresh", "pid": 5678}

    monkeypatch.setattr(wake, "pid_alive", lambda pid: False)
    monkeypatch.setattr(wake, "spawn_role_window", fake_spawn)

    result = wake.notify(tmp_path, file=wake.TARGET_FILE, session_id="sid-doc")

    assert result["action"] == "spawned"
    assert spawned == ["dev"]
    assert _read_registry(tmp_path)["roles"]["dev"]["pid"] == 5678


def test_notify_delivery_failure_does_not_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = _isolate(monkeypatch, tmp_path)
    _write_registry(
        tmp_path,
        roles={"dev": {"session": "dev", "ref": "sid-dev", "pid": 1234, "updated_at": "x"}},
        sessions={"sid-doc": "doc"},
    )
    _write_session(
        config_dir,
        pid=1234,
        session_id="sid-dev",
        socket_path=r"\\.\pipe\LOCAL\cc-msg-dev",
        name="dev",
    )
    spawned: list[str] = []
    monkeypatch.setattr(wake, "pid_alive", lambda pid: True)
    monkeypatch.setattr(wake, "deliver_peer_message", _fake_deliver([], (False, "busy")))
    monkeypatch.setattr(wake, "spawn_role_window", lambda root, role: spawned.append(role))

    result = wake.notify(tmp_path, file=wake.TARGET_FILE, session_id="sid-doc")

    assert result["action"] == "delivery-failed"
    assert result["reason"] == "busy"
    assert spawned == []


def test_notify_spawn_failure_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    _write_registry(tmp_path, sessions={"sid-doc": "doc"})
    monkeypatch.setattr(wake, "spawn_role_window", lambda root, role: None)

    result = wake.notify(tmp_path, file=wake.TARGET_FILE, session_id="sid-doc")

    assert result["action"] == "spawn-failed"


def test_notify_cli_non_target_is_noop_and_leaves_no_registry(tmp_path: Path) -> None:
    result = _run(tmp_path, "notify", "--file", "docs/local/交流区/STATUS.md")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["action"] == "noop"
    assert not _registry_file(tmp_path).exists()


@pytest.mark.skipif(os.name != "nt", reason="frozen down-start uses Windows PowerShell")
def test_spawn_role_window_parses_register_marker(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "dev_start.ps1").write_text(
        "Write-Output 'SENTINEL_REGISTER:"
        '{"role":"dev","session":"agentsentinel-test","ref":"sid-dev","pid":1234}' + "'\n",
        encoding="utf-8",
    )

    entry = wake.spawn_role_window(tmp_path, "dev")

    assert entry is not None
    assert entry["role"] == "dev"
    assert entry["session"] == "agentsentinel-test"
    assert entry["ref"] == "sid-dev"
    assert entry["pid"] == 1234
