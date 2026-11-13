"""EngineAdapter 与 Workspace 配置中心：预检、CRUD、激活隔离。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from tools.orchestrator import service
from tools.orchestrator.engines import adapter_for, preflight
from tools.orchestrator.service import _pty_environment
from tools.orchestrator.workspaces import WorkspaceStore


def test_preflight_detects_missing_command(tmp_path: Path) -> None:
    result = preflight("definitely-not-a-command-xyz", str(tmp_path))
    assert result["ok"] is False
    assert result["reason"] == "command_not_found"
    assert "未检测到" in result["error"]


def test_preflight_detects_command_and_path(tmp_path: Path) -> None:
    result = preflight(sys.executable, str(tmp_path))
    assert result["ok"] is True, result
    assert result["command_path"]
    assert Path(result["project_path"]).is_dir()


def test_preflight_rejects_missing_path() -> None:
    result = preflight(sys.executable, r"D:\definitely\missing\path")
    assert result["ok"] is False
    assert result["reason"] == "path_not_found"


def test_pty_environment_inherits_os_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "C:\\custom-path")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "outer-session")
    env = _pty_environment()
    assert env["PATH"] == "C:\\custom-path"
    assert "CLAUDE_CODE_SESSION_ID" not in env


def test_engine_build_command_includes_session_for_claude() -> None:
    claude = adapter_for("claude")
    command = claude.build_command("claude --foo", "session-1")
    assert command == ["claude", "--foo", "--session-id", "session-1"]
    assert claude.capabilities() == {"chat": True, "usage": True, "clear": True}

    generic = adapter_for("codex")
    assert generic.build_command("codex exec", "session-1") == ["codex", "exec"]
    assert generic.capabilities() == {"chat": False, "usage": False, "clear": False}


def test_workspace_store_crud_and_active(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces.sqlite3")
    default = store.seed_default(str(tmp_path))
    assert default["name"] == "AgentSentinel"
    assert default["active"] == 1
    assert default["daemon_home"] == str(tmp_path / "orchestrator")
    assert len(store.list()) == 1

    created = store.save(
        {
            "name": "Other",
            "project_path": str(tmp_path),
            "command": "codex",
            "engine": "codex",
            "wake_prompt": "接续当前进度",
            "budget_tokens": 1000,
        },
        activate=True,
    )
    assert created["active"] == 1
    assert created["capabilities"]["chat"] is False
    assert created["daemon_home"] == str(tmp_path / "workspaces" / str(created["id"]))
    active = store.active()
    assert active is not None
    assert active["id"] == created["id"]

    updated = store.save({**created, "name": "Other 2"})
    assert updated["name"] == "Other 2"

    assert store.delete(created["id"]) is True
    remaining = store.list()
    assert len(remaining) == 1
    assert remaining[0]["id"] == default["id"]
    active = store.active()
    assert active is not None
    assert active["id"] == default["id"]


def test_workspace_store_requires_fields(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces.sqlite3")
    for payload in (
        {"name": "", "project_path": str(tmp_path), "command": "claude"},
        {"name": "x", "project_path": "", "command": "claude"},
        {"name": "x", "project_path": str(tmp_path), "command": "", "engine": "generic"},
    ):
        try:
            store.save(payload)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {payload}")


def test_workspace_db_uses_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTINEL_WORKSPACES_DB", raising=False)
    monkeypatch.delenv("SENTINEL_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    control = service.PROJECT_ROOT / ".claude" / "orchestrator"
    assert service.workspace_db_path(control) == (
        tmp_path / "appdata" / "AgentSentinel" / "workspaces.sqlite3"
    )


def test_ensure_workspace_db_migrates_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    legacy = root / ".claude" / "workspaces.sqlite3"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy-workspaces")
    monkeypatch.setattr(service, "PROJECT_ROOT", root)
    monkeypatch.delenv("SENTINEL_WORKSPACES_DB", raising=False)
    monkeypatch.delenv("SENTINEL_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    path = service.ensure_workspace_db(root / ".claude" / "orchestrator")

    assert path == tmp_path / "appdata" / "AgentSentinel" / "workspaces.sqlite3"
    assert path.read_bytes() == b"legacy-workspaces"


def test_workspace_store_respects_control_home(tmp_path: Path) -> None:
    control = tmp_path / "control"
    store = WorkspaceStore(tmp_path / "data" / "workspaces.sqlite3", control_home=control)
    default = store.seed_default(str(tmp_path))
    assert default["daemon_home"] == str(control)

    other = store.save(
        {
            "name": "Other",
            "project_path": str(tmp_path),
            "command": "claude",
            "engine": "claude",
        },
        activate=True,
    )
    assert other["daemon_home"] == str(tmp_path / "data" / "workspaces" / str(other["id"]))
