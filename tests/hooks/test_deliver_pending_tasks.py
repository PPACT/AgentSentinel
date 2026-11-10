"""Stop Hook：领取外部任务并以 block 唤醒会话。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from tests.hooks.hook_utils import run_hook


class _TaskServer(BaseHTTPRequestHandler):
    tasks: ClassVar[list[dict[str, Any]]] = []
    paths: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        type(self).paths.append(self.path)
        body = json.dumps({"tasks": type(self).tasks}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def task_server() -> Any:
    _TaskServer.tasks = []
    _TaskServer.paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TaskServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()


def _home(tmp_path: Path, port: int) -> Path:
    home = tmp_path / "orch"
    home.mkdir()
    (home / "daemon.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": port}), encoding="utf-8"
    )
    (home / "token").write_text("fixture-token", encoding="utf-8")
    return home


def _run(home: Path, payload: dict[str, Any], *, role: str = "dev") -> Any:
    return run_hook(
        "deliver_pending_tasks.py",
        payload,
        env={"SENTINEL_ROLE": role, "SENTINEL_ORCH_HOME": str(home)},
    )


def test_blocks_when_delivered_task_exists(tmp_path: Path, task_server: int) -> None:
    _TaskServer.tasks = [
        {"task_id": "t1", "to_role": "dev", "intent": "implement", "pointer": "docs/x.md"}
    ]
    home = _home(tmp_path, task_server)

    result = _run(home, {"hook_event_name": "Stop"})

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["decision"] == "block"
    assert "t1" in output["reason"]
    assert any("role=dev" in path and "status=delivered" in path for path in _TaskServer.paths)


def test_skips_when_stop_hook_active(tmp_path: Path, task_server: int) -> None:
    _TaskServer.tasks = [{"task_id": "t1", "to_role": "dev"}]
    home = _home(tmp_path, task_server)

    result = _run(home, {"hook_event_name": "Stop", "stop_hook_active": True})

    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert _TaskServer.paths == []


def test_silent_when_no_task(tmp_path: Path, task_server: int) -> None:
    home = _home(tmp_path, task_server)

    result = _run(home, {"hook_event_name": "Stop"})

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_filters_tasks_for_other_roles(tmp_path: Path, task_server: int) -> None:
    _TaskServer.tasks = [{"task_id": "t2", "to_role": "doc"}]
    home = _home(tmp_path, task_server)

    result = _run(home, {"hook_event_name": "Stop"})

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_reads_role_from_session_cache(tmp_path: Path, task_server: int) -> None:
    _TaskServer.tasks = [{"task_id": "t3", "to_role": "dev", "intent": "test"}]
    home = _home(tmp_path, task_server)
    agents = home / "agents"
    agents.mkdir()
    (agents / "session-cache.json").write_text(
        json.dumps({"session_id": "session-cache", "role": "dev"}), encoding="utf-8"
    )

    result = run_hook(
        "deliver_pending_tasks.py",
        {"hook_event_name": "Stop", "session_id": "session-cache"},
        env={"SENTINEL_ROLE": "", "SENTINEL_ORCH_HOME": str(home)},
    )

    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["decision"] == "block"
    assert "t3" in output["reason"]
