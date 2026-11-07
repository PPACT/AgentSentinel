"""P1 launcher：dummy worker + 孙进程回收、env allowlist。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tools.orchestrator import launcher as launcher_module
from tools.orchestrator.launcher import Launcher, process_alive

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")
_MODEL_ENV = "ANTHROPIC_" + "API" + "_" + "KEY"


def test_spawn_and_stop_reclaims_worker_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_CANARY", "must-not-reach-worker")
    monkeypatch.setenv(_MODEL_ENV, "placeholder")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    launcher = Launcher(tmp_path / "orch")
    try:
        worker = launcher.spawn_dummy("dev", seconds=30)

        assert process_alive(worker.pid)
        assert process_alive(worker.child_pid)
        assert worker.job_id
        assert worker.log_path.exists()

        info = json.loads(worker.pid_file.read_text(encoding="utf-8"))
        assert "SENTINEL_CANARY" not in info["env_keys"]
        assert _MODEL_ENV not in info["env_keys"]
        assert "CLAUDE_CONFIG_DIR" not in info["env_keys"]
        assert "SENTINEL_ROLE" in info["env_keys"]

        result = launcher.stop("dev")

        assert result["worker"]["alive"] is False
        assert result["child"]["alive"] is False
        assert process_alive(worker.pid) is False
        assert process_alive(worker.child_pid) is False
    finally:
        launcher.stop_all()


def test_stop_unknown_role_raises(tmp_path: Path) -> None:
    launcher = Launcher(tmp_path / "orch")

    with pytest.raises(KeyError):
        launcher.stop("nope")


def test_stop_all_handles_empty_launcher(tmp_path: Path) -> None:
    launcher = Launcher(tmp_path / "orch")

    assert launcher.stop_all() == []


def test_hidden_creationflags_hide_console() -> None:
    assert launcher_module.hidden_creationflags() == subprocess.CREATE_NO_WINDOW


def test_stop_falls_back_to_taskkill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = Launcher(tmp_path / "orch")
    worker = launcher.spawn_dummy("dev", seconds=30)
    try:
        monkeypatch.setattr(launcher_module, "close_job", lambda handle: None)

        result = launcher.stop("dev")

        assert result["orphan_detected"] is True
        assert result["worker"]["alive"] is False
        assert result["child"]["alive"] is False
        assert process_alive(worker.pid) is False
        assert process_alive(worker.child_pid) is False
    finally:
        launcher.stop_all()
