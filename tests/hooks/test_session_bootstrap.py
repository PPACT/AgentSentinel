"""session_bootstrap（SessionStart）：env / marker / no-op 三分支，全程静默。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.hooks.hook_utils import run_hook

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

import common
import session_bootstrap


def _payload(session_id: str = "sid-1") -> dict[str, Any]:
    return {"hook_event_name": "SessionStart", "session_id": session_id}


def _registry_file(project_dir: Path) -> Path:
    return project_dir / ".claude" / "mailbox" / "registry.json"


def _read_registry(project_dir: Path) -> dict[str, Any]:
    path = _registry_file(project_dir)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _isolate(monkeypatch: pytest.MonkeyPatch, project_dir: Path) -> None:
    monkeypatch.setattr(common, "PROJECT_ROOT", project_dir)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.delenv("SENTINEL_ROLE", raising=False)


def _write_claim(project_dir: Path, text: str) -> None:
    claim = project_dir / ".claude" / "mailbox" / "role.claim"
    claim.parent.mkdir(parents=True, exist_ok=True)
    claim.write_text(text, encoding="utf-8")


def test_env_role_registers_session_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("SENTINEL_ROLE", "dev")

    result = session_bootstrap.handle(_payload("sid-env"))

    assert result is not None
    data = _read_registry(tmp_path)
    assert data["roles"]["dev"]["session"] == "sid-env"
    assert data["roles"]["dev"]["ref"] == ""
    assert "sessions" not in data


def test_marker_role_registers_when_env_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _write_claim(tmp_path, "\n doc \nsecond line\n")

    result = session_bootstrap.handle(_payload("sid-marker"))

    assert result is not None
    assert _read_registry(tmp_path)["roles"]["doc"]["session"] == "sid-marker"


def test_env_role_wins_over_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    _write_claim(tmp_path, "doc\n")
    monkeypatch.setenv("SENTINEL_ROLE", "dev")

    session_bootstrap.handle(_payload("sid-both"))

    assert set(_read_registry(tmp_path)["roles"]) == {"dev"}


def test_no_role_is_silent_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    assert session_bootstrap.handle(_payload()) is None
    assert not _registry_file(tmp_path).exists()


def test_invalid_marker_is_silent_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    _write_claim(tmp_path, "bad role!\n")

    assert session_bootstrap.handle(_payload()) is None
    assert not _registry_file(tmp_path).exists()


def test_subprocess_with_env_registers_and_prints_nothing(tmp_path: Path) -> None:
    result = run_hook(
        "session_bootstrap.py",
        _payload("sid-sub"),
        project_dir=tmp_path,
        env={"SENTINEL_ROLE": "dev"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert _read_registry(tmp_path)["roles"]["dev"]["session"] == "sid-sub"


def test_subprocess_without_role_prints_nothing(tmp_path: Path) -> None:
    result = run_hook(
        "session_bootstrap.py",
        _payload(),
        project_dir=tmp_path,
        env={"SENTINEL_ROLE": ""},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert not _registry_file(tmp_path).exists()
