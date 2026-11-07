"""P3：ACK 超时租约、心跳失联、steps/effects、启动恢复。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tools.orchestrator.launcher import process_alive
from tools.orchestrator.registry import AgentRecord, Registry, utc_now
from tools.orchestrator.router import TaskStore

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")

ROOT = Path(__file__).resolve().parents[2]
FUTURE = "9999-01-01T00:00:00.000000Z"


def _read_info(home: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((home / "daemon.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _decode(raw: bytes) -> dict[str, Any]:
    data = json.loads(raw.decode("utf-8") or "{}")
    return data if isinstance(data, dict) else {}


def _api(
    home: Path,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    info = _read_info(home)
    assert info is not None
    headers = {"Authorization": "Bearer " + (home / "token").read_text(encoding="utf-8").strip()}
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://{info['host']}:{info['port']}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status), _decode(response.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code), _decode(exc.read())


def _wait_healthy(home: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _read_info(home) is not None:
            try:
                status, body = _api(home, "GET", "/health")
            except OSError:
                pass
            else:
                if status == 200 and body.get("ok"):
                    return
        time.sleep(0.1)
    raise AssertionError("daemon did not become healthy")


class DaemonProcess:
    def __init__(self, home: Path, process: subprocess.Popen[bytes]) -> None:
        self.home = home
        self.process = process

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()


@pytest.fixture
def daemon_factory(tmp_path: Path) -> Iterator[Any]:
    created: list[DaemonProcess] = []

    def start(
        *,
        home: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> Path:
        target = home or (tmp_path / f"orch-{len(created)}")
        target.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["SENTINEL_ORCH_HOME"] = str(target)
        environment["PYTHONUTF8"] = "1"
        if env:
            environment.update(env)
        log_path = tmp_path / f"daemon-p3-{len(created)}.log"
        with log_path.open("w", encoding="utf-8") as stream:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tools.orchestrator.service",
                    "--serve",
                    "--port",
                    "0",
                    "--home",
                    str(target),
                ],
                cwd=ROOT,
                env=environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        created.append(DaemonProcess(target, process))
        _wait_healthy(target)
        return target

    yield start
    for daemon in created:
        daemon.stop()


def _need(home: Path, *, key: str = "k1", to_role: str = "dev") -> tuple[int, dict[str, Any]]:
    return _api(
        home,
        "POST",
        "/need",
        {
            "from_role": "doc",
            "to_role": to_role,
            "intent": "implement",
            "pointer": "docs/local/交流区/交流区.md#p3",
            "idempotency_key": key,
        },
    )


def _wait_status(home: Path, task_id: str, status: str, timeout: float = 8.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        code, body = _api(home, "POST", "/task", {"task_id": task_id})
        assert code == 200, body
        if body["task"]["status"] == status:
            return body
        time.sleep(0.1)
    raise AssertionError(f"task {task_id} not {status}: {body}")


def test_ack_before_timeout_scan_is_not_killed(daemon_factory: Any) -> None:
    home = daemon_factory(env={"SENTINEL_ORCH_ACK_TIMEOUT_SECONDS": "1.0"})
    code, body = _need(home)
    assert code == 200
    task_id = body["task"]["task_id"]
    _, status = _api(home, "GET", "/status")
    pid = int(status["agents"][0]["pid"])

    time.sleep(0.4)
    code, acked = _api(home, "POST", "/ack", {"task_id": task_id, "role": "dev"})
    assert code == 200
    assert acked["task"]["status"] == "running"

    time.sleep(2.0)
    _, detail = _api(home, "POST", "/task", {"task_id": task_id})
    assert detail["task"]["status"] == "running"
    assert [message["type"] for message in detail["messages"]] == ["need", "ack"]
    assert process_alive(pid)


def test_heartbeat_lost_marks_agent_and_task_failed(daemon_factory: Any) -> None:
    home = daemon_factory(
        env={
            "SENTINEL_ORCH_LOST_SECONDS": "1",
            "SENTINEL_ORCH_HEARTBEAT_SECONDS": "10",
        }
    )
    code, body = _need(home)
    assert code == 200
    task_id = body["task"]["task_id"]
    _, status = _api(home, "GET", "/status")
    pid = int(status["agents"][0]["pid"])

    detail = _wait_status(home, task_id, "failed", timeout=8.0)

    failed = [message for message in detail["messages"] if message["type"] == "failed"]
    assert failed
    assert failed[-1]["payload"]["reason"] == "heartbeat_lost"
    _, status = _api(home, "GET", "/status")
    assert status["agents"][0]["status"] == "failed"
    assert status["agents"][0]["alive"] is False
    assert process_alive(pid) is False


def test_step_upsert_and_effect_dedupe(daemon_factory: Any) -> None:
    home = daemon_factory()
    code, body = _need(home)
    assert code == 200
    task_id = body["task"]["task_id"]

    first = _api(
        home,
        "POST",
        "/step",
        {"task_id": task_id, "step_no": 1, "status": "pending"},
    )
    assert first[0] == 200
    second = _api(
        home,
        "POST",
        "/step",
        {
            "task_id": task_id,
            "step_no": 1,
            "status": "done",
            "output_ref": "workspace/step-1",
        },
    )
    assert second[0] == 200
    _, steps = _api(home, "GET", f"/steps?task_id={task_id}")
    assert len(steps["steps"]) == 1
    assert steps["steps"][0]["status"] == "done"
    assert steps["steps"][0]["output_ref"] == "workspace/step-1"

    effect = {
        "task_id": task_id,
        "idem_key": "effect-1",
        "action": "write",
        "target": "workspace/out.txt",
    }
    code, created = _api(home, "POST", "/effect", effect)
    assert code == 200
    assert created["created"] is True
    code, duplicate = _api(home, "POST", "/effect", effect)
    assert code == 200
    assert duplicate["created"] is False

    _, single = _api(home, "GET", "/effect?idem_key=effect-1")
    assert single["effect"]["idem_key"] == "effect-1"
    _, listed = _api(home, "GET", f"/effect?task_id={task_id}")
    assert len(listed["effects"]) == 1


def test_startup_recovery_fails_leftover_tasks_and_agents(
    tmp_path: Path, daemon_factory: Any
) -> None:
    home = tmp_path / "recover"
    home.mkdir()
    now = utc_now()
    Registry(home / "registry.sqlite3").upsert(
        AgentRecord(
            role="dev",
            pid=999999,
            pgid=999999,
            job_id="stale",
            status="running",
            started_at=now,
            last_heartbeat=now,
            command="dummy_worker --role dev",
            updated_at=now,
        )
    )
    store = TaskStore(home / "registry.sqlite3")
    store.create_or_get(
        task_id="T-leftover",
        from_role="doc",
        to_role="dev",
        intent="implement",
        pointer="p",
        acceptance=[],
        idempotency_key="leftover",
        max_tokens=None,
        max_minutes=None,
    )
    store.mark_delivered("T-leftover")
    store.mark_running("T-leftover", "dev")

    daemon_factory(home=home)

    code, detail = _api(home, "POST", "/task", {"task_id": "T-leftover"})
    assert code == 200
    assert detail["task"]["status"] == "failed"
    assert any(
        message["type"] == "failed" and message["payload"].get("reason") == "daemon_restart"
        for message in detail["messages"]
    )
    _, status = _api(home, "GET", "/status")
    assert status["agents"][0]["status"] == "lost"


def test_claim_timeout_respects_ack(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "db.sqlite3")
    running, _ = store.create_or_get(
        task_id="T-running",
        from_role="doc",
        to_role="dev",
        intent="x",
        pointer="p",
        acceptance=[],
        idempotency_key="running",
        max_tokens=None,
        max_minutes=None,
    )
    store.mark_delivered(running.task_id)
    store.mark_running(running.task_id, "dev")
    assert store.claim_timeout(running.task_id, FUTURE) is None

    delivered, _ = store.create_or_get(
        task_id="T-delivered",
        from_role="doc",
        to_role="doc",
        intent="x",
        pointer="p",
        acceptance=[],
        idempotency_key="delivered",
        max_tokens=None,
        max_minutes=None,
    )
    store.mark_delivered(delivered.task_id)
    claimed = store.claim_timeout(delivered.task_id, FUTURE)
    assert claimed is not None
    assert claimed.status == "timeout_pending"

    acked = store.mark_running(delivered.task_id, "doc")
    assert acked.status == "running"
    assert store.claim_timeout(delivered.task_id, FUTURE) is None
