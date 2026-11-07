"""scripts/wake.py（v3）：register / resolve、registry 原子写、不越项目边界。"""

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
    process_env["HOME"] = str(project_dir / "home-outside")
    process_env["USERPROFILE"] = str(project_dir / "home-outside")
    process_env["PYTHONUTF8"] = "1"
    process_env.pop("SENTINEL_ROLE", None)
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


def _write_registry(project_dir: Path, payload: dict[str, Any]) -> None:
    path = _registry_file(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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
    )

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["registered"] is True
    assert out["registration"]["role"] == "doc"
    data = _read_registry(tmp_path)
    assert data["version"] == 1
    assert data["roles"]["doc"]["session"] == "agentsentinel-doc"
    assert data["roles"]["doc"]["ref"] == "abcd12"
    assert data["roles"]["doc"]["updated_at"]
    assert "pid" not in data["roles"]["doc"]
    assert "sessions" not in data
    assert not (tmp_path / ".claude" / "mailbox" / "roles").exists()


def test_register_upserts_without_conflict(tmp_path: Path) -> None:
    first = _run(tmp_path, "register", "--role", "dev", "--session", "old", "--ref", "a1")
    second = _run(tmp_path, "register", "--role", "dev", "--session", "new", "--ref", "b2")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    data = _read_registry(tmp_path)
    assert data["roles"]["dev"]["session"] == "new"
    assert data["roles"]["dev"]["ref"] == "b2"


def test_register_normalizes_role_case(tmp_path: Path) -> None:
    result = _run(tmp_path, "register", "--role", "DEV", "--session", "d")

    assert result.returncode == 0, result.stderr
    assert "dev" in _read_registry(tmp_path)["roles"]


def test_register_rejects_invalid_role(tmp_path: Path) -> None:
    result = _run(tmp_path, "register", "--role", "bad role!", "--session", "d")

    assert result.returncode == 1
    assert "非法角色名" in result.stderr
    assert not _registry_file(tmp_path).exists()


def test_resolve_returns_entry_and_missing(tmp_path: Path) -> None:
    registered = _run(
        tmp_path, "register", "--role", "doc", "--session", "doc-window", "--ref", "r1"
    )
    assert registered.returncode == 0, registered.stderr

    result = _run(tmp_path, "resolve", "--role", "doc")

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["role"] == "doc"
    assert out["session"] == "doc-window"
    assert out["ref"] == "r1"
    assert "alive" not in out
    assert "pid" not in out

    missing = _run(tmp_path, "resolve", "--role", "dev")
    assert missing.returncode == 1
    assert missing.stdout.strip() == "{}"


def test_corrupt_registry_degrades_to_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    path = _registry_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    data = wake.load_registry(tmp_path)

    assert data["roles"] == {}
    assert "sessions" not in data


def test_legacy_sessions_key_is_dropped(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "version": 1,
            "updated_at": "2026-11-07T00:00:00Z",
            "roles": {},
            "sessions": {"sid": "doc"},
        },
    )

    result = _run(tmp_path, "register", "--role", "dev", "--session", "d")

    assert result.returncode == 0, result.stderr
    assert "sessions" not in _read_registry(tmp_path)


def test_registry_write_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    wake.register_role(tmp_path, role="doc", session="doc-1")

    def fail_replace(source: object, target: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    try:
        wake.register_role(tmp_path, role="dev", session="dev-1")
    except OSError:
        pass
    else:  # pragma: no cover - 测试守卫
        raise AssertionError("replace 失败时 register 应抛出 OSError")

    data = _read_registry(tmp_path)
    assert list(data["roles"]) == ["doc"]
    mailbox = tmp_path / ".claude" / "mailbox"
    assert not list(mailbox.glob("*.tmp-*"))
    assert not list(mailbox.glob("*.lock"))


def test_register_never_touches_outside_paths(tmp_path: Path) -> None:
    result = _run(tmp_path, "register", "--role", "dev", "--session", "d")

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "home-outside").exists()
    for removed in (
        "sessions_dir",
        "claude_config_dir",
        "pid_alive",
        "notify",
        "spawn_role_window",
        "deliver_peer_message",
    ):
        assert not hasattr(wake, removed), f"{removed} 应已随 v3 删除"


def test_cli_exposes_only_register_and_resolve() -> None:
    result = _run(REPO_ROOT, "--help")

    assert result.returncode == 0, result.stderr
    assert "register" in result.stdout
    assert "resolve" in result.stdout
    for removed in ("notify", "spawn", "pid"):
        assert removed not in result.stdout
