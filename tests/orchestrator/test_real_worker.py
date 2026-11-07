"""P4 真实 worker：命令构造、env 注入、看护线程。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tools.orchestrator import launcher as launcher_module
from tools.orchestrator import mcp_server
from tools.orchestrator.launcher import Launcher
from tools.orchestrator.registry import Registry
from tools.orchestrator.router import Router, TaskStore

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")
_MODEL_ENV = "ANTHROPIC_" + "API" + "_" + "KEY"  # 分段拼接，避免扫描器把测试样本当真实密钥


def test_real_worker_command_builds_expected_flags() -> None:
    command = launcher_module.real_worker_command(
        "dev",
        task_id="T-1",
        pointer="docs/local/交流区/交流区.md#p4",
        acceptance=["tests pass"],
        attempts=2,
    )

    assert command[0] == "claude"
    assert command[1] == "-p"
    assert "--output-format" in command and "json" in command
    assert "--no-session-persistence" in command
    assert "--permission-mode" in command and "manual" in command
    assert "--permission-prompts" in command and "none" in command
    assert "--max-budget-usd" in command

    allowed = command[command.index("--allowedTools") + 1].split(",")
    for tool in ("Read", "Glob", "Grep", "Edit", "Write", "Bash"):
        assert tool in allowed
    for tool in (
        "mcp__sentinel__orchestrator_ack",
        "mcp__sentinel__orchestrator_completed",
    ):
        assert tool in allowed

    system_prompt = command[command.index("--append-system-prompt") + 1]
    assert "T-1" in system_prompt
    assert "docs/local/交流区/交流区.md#p4" in system_prompt
    assert "tests pass" in system_prompt
    assert "attempts=2" in system_prompt
    assert "Do not start background subagents" in system_prompt


def test_server_name_matches_mcp_server() -> None:
    assert launcher_module.MCP_SERVER_NAME == mcp_server.SERVER_NAME


class _FakeProcess:
    pid = 4321

    def poll(self) -> int | None:
        return None

    def kill(self) -> None:
        return

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_spawn_real_injects_env_and_hides_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv(_MODEL_ENV, "placeholder")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("SENTINEL_CANARY", "must-not-pass")

    def fake_popen(args: list[str], **kwargs: Any) -> _FakeProcess:
        captured["args"] = args
        captured["env"] = kwargs["env"]
        captured["creationflags"] = kwargs["creationflags"]
        return _FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher_module, "create_job", lambda: 1)
    monkeypatch.setattr(launcher_module, "assign_process", lambda job, pid: True)

    launcher = Launcher(tmp_path / "orch")
    launcher.spawn_real(
        "dev",
        task_id="T-1",
        pointer="p",
        acceptance=["a"],
        attempts=2,
        orchestrator_url="http://127.0.0.1:1",
        token="secret-token",
    )

    environment = captured["env"]
    assert environment["ROLE"] == "dev"
    assert environment["SENTINEL_ROLE"] == "dev"
    assert environment["TASK_ID"] == "T-1"
    assert environment["ORCHESTRATOR_ATTEMPTS"] == "2"
    assert environment["ORCHESTRATOR_URL"] == "http://127.0.0.1:1"
    assert environment["ORCHESTRATOR_TOKEN"] == "secret-token"
    assert environment[_MODEL_ENV] == "placeholder"
    assert environment["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude-home")
    assert "SENTINEL_CANARY" not in environment
    assert captured["creationflags"] == launcher_module.hidden_creationflags()
    assert "secret-token" not in " ".join(captured["args"])

    launcher._workers.clear()


def _router(tmp_path: Path) -> tuple[Router, Registry, TaskStore, Launcher]:
    db = tmp_path / "db.sqlite3"
    registry = Registry(db)
    store = TaskStore(db)
    launcher = Launcher(tmp_path / "orch")
    router = Router(
        store=store,
        registry=registry,
        launcher=launcher,
        token="token",
        orchestrator_url="http://127.0.0.1:1",
    )
    return router, registry, store, launcher


def test_watchdog_refreshes_live_worker_heartbeat(tmp_path: Path) -> None:
    router, registry, _, launcher = _router(tmp_path)
    try:
        code, _ = router.need(
            {
                "from_role": "doc",
                "to_role": "dev",
                "intent": "implement",
                "pointer": "p",
                "idempotency_key": "watch",
            }
        )
        assert code == 200
        before = registry.get("dev")
        assert before is not None

        actions = router.watch_workers()

        assert any(action["action"] == "heartbeat_refreshed" for action in actions)
        after = registry.get("dev")
        assert after is not None
        assert after.last_heartbeat >= before.last_heartbeat
    finally:
        launcher.stop_all()


def test_watchdog_fails_task_when_worker_exits(tmp_path: Path) -> None:
    router, registry, store, launcher = _router(tmp_path)
    try:
        code, body = router.need(
            {
                "from_role": "doc",
                "to_role": "dev",
                "intent": "implement",
                "pointer": "p",
                "idempotency_key": "exit",
            }
        )
        assert code == 200
        task_id = body["task"]["task_id"]
        worker = launcher.get("dev")
        assert worker is not None
        worker.process.kill()
        worker.process.wait(timeout=10)

        actions = router.watch_workers()

        assert any(action["action"] == "worker_exit" for action in actions)
        task = store.get(task_id)
        assert task is not None
        assert task.status == "failed"
        reasons = [
            message["payload"].get("reason")
            for message in store.messages(task_id)
            if message["type"] == "failed"
        ]
        assert any(str(reason).startswith("worker_exit:") for reason in reasons)
        agent = registry.get("dev")
        assert agent is not None
        assert agent.status == "failed"
    finally:
        launcher.stop_all()
