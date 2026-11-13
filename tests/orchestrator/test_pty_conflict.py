"""PTY session 冲突：识别占用报错、自动换新 session、限次重试。"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from tools.orchestrator.pty_manager import (
    MAX_SESSION_RETRIES,
    PtyManager,
    session_conflict_detected,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="ConPTY on Windows")

CONFLICT_TEXT = "Error: Session ID is already in use"


def _factory(
    seen: list[str], marker: Path, *, always_conflict: bool = False
) -> Callable[[str], list[str]]:
    code = (
        "import pathlib, sys, time\n"
        "marker = pathlib.Path(sys.argv[1])\n"
        "if not marker.exists() or " + ("True" if always_conflict else "False") + ":\n"
        "    marker.write_text('1')\n"
        "    print('" + CONFLICT_TEXT + "', flush=True)\n"
        "    raise SystemExit(1)\n"
        "time.sleep(5)\n"
    )

    def factory(session_id: str) -> list[str]:
        seen.append(session_id)
        return [sys.executable, "-c", code, str(marker)]

    return factory


def test_conflict_marker_detection() -> None:
    assert session_conflict_detected(b"Error: Session ID is already in use")
    assert session_conflict_detected(b"session already exists")
    assert not session_conflict_detected(b"all good, session started")


def test_conflict_retries_with_new_session(tmp_path: Path) -> None:
    seen: list[str] = []
    manager = PtyManager(
        cwd=str(tmp_path),
        command_factory=_factory(seen, tmp_path / "seen.txt"),
    )
    try:
        manager.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if manager.status()["running"] and len(seen) >= 2:
                break
            time.sleep(0.1)
        assert len(seen) == 2, seen
        assert seen[0] != seen[1]
        assert manager.session_id == seen[1]
        notice = manager.latest_notice()
        assert notice is not None
        assert notice["kind"] == "session_conflict"
        assert "全新干净会话" in notice["message"]
    finally:
        manager.stop()


def test_conflict_retry_is_bounded(tmp_path: Path) -> None:
    seen: list[str] = []
    manager = PtyManager(
        cwd=str(tmp_path),
        command_factory=_factory(seen, tmp_path / "seen.txt", always_conflict=True),
    )
    try:
        manager.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and manager.status()["running"]:
            time.sleep(0.1)
        assert manager.status()["running"] is False
        assert len(seen) == MAX_SESSION_RETRIES + 1, seen
        notice = manager.latest_notice()
        assert notice is not None
        assert notice["kind"] == "session_conflict"
    finally:
        manager.stop()
