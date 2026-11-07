"""wake_on_exchange（PostToolUse）：路径过滤、恰好一次 notify、零输出。"""

from __future__ import annotations

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
import wake_on_exchange
from scripts import wake as wake_module


def _payload(
    project_dir: Path,
    *,
    tool: str = "Edit",
    rel_path: str = wake_on_exchange.TARGET_FILE,
    session_id: str = "sid-doc",
) -> dict[str, Any]:
    target = project_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(target)},
        "cwd": str(project_dir),
        "session_id": session_id,
    }


def _install_notify(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[Path, str, str]],
) -> None:
    def fake_notify(root: Path, *, file: str, session_id: str = "") -> dict[str, Any]:
        calls.append((root, file, session_id))
        return {"action": "noop"}

    monkeypatch.setattr(wake_module, "notify", fake_notify)


def test_target_file_calls_notify_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    calls: list[tuple[Path, str, str]] = []
    _install_notify(monkeypatch, calls)

    wake_on_exchange.handle(_payload(tmp_path))

    assert len(calls) == 1
    assert calls[0][0] == tmp_path
    assert calls[0][1] == wake_on_exchange.TARGET_FILE
    assert calls[0][2] == "sid-doc"


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_all_write_tools_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    calls: list[tuple[Path, str, str]] = []
    _install_notify(monkeypatch, calls)

    wake_on_exchange.handle(_payload(tmp_path, tool=tool))

    assert len(calls) == 1


def test_status_file_is_not_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    calls: list[tuple[Path, str, str]] = []
    _install_notify(monkeypatch, calls)

    wake_on_exchange.handle(_payload(tmp_path, rel_path="docs/local/交流区/STATUS.md"))

    assert calls == []


def test_other_file_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    calls: list[tuple[Path, str, str]] = []
    _install_notify(monkeypatch, calls)

    wake_on_exchange.handle(_payload(tmp_path, rel_path="src/app.py"))

    assert calls == []


def test_outside_project_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    calls: list[tuple[Path, str, str]] = []
    _install_notify(monkeypatch, calls)
    outside = tmp_path.parent / "outside.md"
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(outside)},
        "cwd": str(tmp_path),
        "session_id": "sid-doc",
    }

    wake_on_exchange.handle(payload)

    assert calls == []


def test_notebook_edit_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    calls: list[tuple[Path, str, str]] = []
    _install_notify(monkeypatch, calls)

    wake_on_exchange.handle(_payload(tmp_path, tool="NotebookEdit"))

    assert calls == []


def test_missing_tool_input_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    calls: list[tuple[Path, str, str]] = []
    _install_notify(monkeypatch, calls)

    wake_on_exchange.handle({"tool_name": "Edit"})

    assert calls == []


def test_handle_prints_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    _install_notify(monkeypatch, [])

    wake_on_exchange.handle(_payload(tmp_path))

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_main_swallows_exceptions_and_prints_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    def explode() -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr(common, "read_payload", explode)

    wake_on_exchange.main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_subprocess_non_target_is_silent(tmp_path: Path) -> None:
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(tmp_path / "README.md")},
        "cwd": str(tmp_path),
    }

    result = run_hook(
        "wake_on_exchange.py",
        payload,
        project_dir=tmp_path,
        env={"SENTINEL_ROLE": ""},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_subprocess_target_without_sender_is_silent(tmp_path: Path) -> None:
    result = run_hook(
        "wake_on_exchange.py",
        _payload(tmp_path),
        project_dir=tmp_path,
        env={"SENTINEL_ROLE": ""},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not (tmp_path / ".claude" / "mailbox" / "registry.json").exists()
