"""P2 任务链路：need → ack → completed、幂等、busy、ACK 超时、token 不外泄。"""

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

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")

ROOT = Path(__file__).resolve().parents[2]


def _read_info(home: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((home / "daemon.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


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


def _decode(raw: bytes) -> dict[str, Any]:
    data = json.loads(raw.decode("utf-8") or "{}")
    return data if isinstance(data, dict) else {}


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
def daemon_factory(
    tmp_path: Path,
) -> Iterator[Any]:
    created: list[DaemonProcess] = []

    def start(*, ack_timeout: float = 120.0) -> Path:
        home = tmp_path / f"orch-{len(created)}"
        environment = os.environ.copy()
        environment["SENTINEL_ORCH_HOME"] = str(home)
        environment["SENTINEL_ORCH_ACK_TIMEOUT_SECONDS"] = str(ack_timeout)
        environment["PYTHONUTF8"] = "1"
        log_path = tmp_path / f"daemon-{len(created)}.log"
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
                    str(home),
                ],
                cwd=ROOT,
                env=environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        created.append(DaemonProcess(home, process))
        _wait_healthy(home)
        return home

    yield start
    for daemon in created:
        daemon.stop()


def _need(
    home: Path,
    *,
    key: str = "k1",
    from_role: str = "doc",
    to_role: str = "dev",
) -> tuple[int, dict[str, Any]]:
    return _api(
        home,
        "POST",
        "/need",
        {
            "from_role": from_role,
            "to_role": to_role,
            "intent": "implement",
            "pointer": "docs/local/交流区/交流区.md#p2",
            "acceptance": ["tests pass"],
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


def test_need_ack_completed_full_chain(daemon_factory: Any) -> None:
    home = daemon_factory()
    code, body = _need(home)
    assert code == 200, body
    task = body["task"]
    assert task["status"] == "delivered"
    assert task["attempts"] == 1
    assert body["idempotent"] is False

    code, status = _api(home, "GET", "/status")
    assert code == 200
    agents = {agent["role"]: agent for agent in status["agents"]}
    assert agents["dev"]["alive"] is True
    dev_pid = int(agents["dev"]["pid"])
    assert process_alive(dev_pid)

    code, body = _api(home, "POST", "/ack", {"task_id": task["task_id"], "role": "dev"})
    assert code == 200, body
    assert body["task"]["status"] == "running"
    assert body["task"]["ack_at"]

    code, body = _api(
        home,
        "POST",
        "/completed",
        {
            "task_id": task["task_id"],
            "role": "dev",
            "artifacts": ["workspace/out.txt"],
            "cost_tokens": 42,
        },
    )
    assert code == 200, body
    assert body["task"]["status"] == "done"
    assert body["task"]["completed_at"]
    assert body["task"]["artifacts"] == ["workspace/out.txt"]
    assert body["task"]["cost_tokens"] == 42
    assert body["reclaim"]["stopped"] is True
    assert process_alive(dev_pid) is False
    _, status = _api(home, "GET", "/status")
    assert status["agents"][0]["status"] == "stopped"
    assert status["agents"][0]["alive"] is False

    code, detail = _api(home, "POST", "/task", {"task_id": task["task_id"]})
    assert code == 200
    assert [message["type"] for message in detail["messages"]] == [
        "need",
        "ack",
        "completed",
    ]

    code, listed = _api(home, "GET", "/tasks?role=dev")
    assert code == 200
    assert [item["task_id"] for item in listed["tasks"]] == [task["task_id"]]


def test_each_task_starts_fresh_worker(daemon_factory: Any) -> None:
    home = daemon_factory()
    _, first = _need(home, key="first")
    assert first["task"]["status"] == "delivered"
    first_id = first["task"]["task_id"]
    _, status = _api(home, "GET", "/status")
    first_pid = int(status["agents"][0]["pid"])

    _api(home, "POST", "/ack", {"task_id": first_id, "role": "dev"})
    _api(
        home,
        "POST",
        "/completed",
        {"task_id": first_id, "role": "dev", "artifacts": [], "cost_tokens": 0},
    )
    assert process_alive(first_pid) is False

    _, second = _need(home, key="second")
    assert second["task"]["status"] == "delivered"
    assert second["task"]["task_id"] != first_id
    _, status = _api(home, "GET", "/status")
    second_pid = int(status["agents"][0]["pid"])

    assert second_pid != first_pid
    assert process_alive(second_pid) is True


def test_duplicate_need_is_idempotent(daemon_factory: Any) -> None:
    home = daemon_factory()
    first_code, first = _need(home, key="same")
    assert first_code == 200
    task_id = first["task"]["task_id"]
    _, status = _api(home, "GET", "/status")
    pid = int(status["agents"][0]["pid"])

    second_code, second = _need(home, key="same")

    assert second_code == 200
    assert second["idempotent"] is True
    assert second["task"]["task_id"] == task_id
    _, status = _api(home, "GET", "/status")
    assert int(status["agents"][0]["pid"]) == pid
    _, detail = _api(home, "POST", "/task", {"task_id": task_id})
    assert [message["type"] for message in detail["messages"]] == ["need"]


def test_busy_role_returns_409(daemon_factory: Any) -> None:
    home = daemon_factory()
    assert _need(home, key="first")[0] == 200

    code, body = _need(home, key="second")

    assert code == 409
    assert "role busy" in str(body.get("error"))


def test_ack_timeout_redelivers_with_new_process_then_timeout(
    daemon_factory: Any,
) -> None:
    home = daemon_factory(ack_timeout=0.5)
    code, body = _need(home, key="timeout")
    assert code == 200
    task_id = body["task"]["task_id"]
    _, status = _api(home, "GET", "/status")
    first_pid = int(status["agents"][0]["pid"])
    assert process_alive(first_pid)

    deadline = time.monotonic() + 8.0
    second_pid = 0
    while time.monotonic() < deadline:
        _, detail = _api(home, "POST", "/task", {"task_id": task_id})
        if "need_retry" in [message["type"] for message in detail["messages"]]:
            _, status = _api(home, "GET", "/status")
            second_pid = int(status["agents"][0]["pid"])
            if second_pid != first_pid:
                break
        time.sleep(0.05)

    assert second_pid > 0
    assert second_pid != first_pid
    assert process_alive(first_pid) is False
    assert process_alive(second_pid) is True

    detail = _wait_status(home, task_id, "timeout", timeout=8.0)

    assert detail["task"]["attempts"] == 2
    assert [message["type"] for message in detail["messages"]] == [
        "need",
        "need_retry",
        "timeout",
    ]
    assert process_alive(second_pid) is False
    _, status = _api(home, "GET", "/status")
    assert status["agents"][0]["status"] == "stopped"
    assert status["agents"][0]["alive"] is False


def test_unknown_task_returns_404(daemon_factory: Any) -> None:
    home = daemon_factory()

    assert _api(home, "POST", "/ack", {"task_id": "missing", "role": "dev"})[0] == 404
    assert _api(home, "POST", "/completed", {"task_id": "missing", "role": "dev"})[0] == 404
    assert _api(home, "POST", "/task", {"task_id": "missing"})[0] == 404


def test_wrong_role_ack_returns_400(daemon_factory: Any) -> None:
    home = daemon_factory()
    _, body = _need(home)
    task_id = body["task"]["task_id"]

    code, _ = _api(home, "POST", "/ack", {"task_id": task_id, "role": "doc"})

    assert code == 400
    _, detail = _api(home, "POST", "/task", {"task_id": task_id})
    assert detail["task"]["status"] == "delivered"


def test_token_never_reaches_registry_or_logs(daemon_factory: Any, tmp_path: Path) -> None:
    home = daemon_factory()
    assert _need(home, key="token-test")[0] == 200
    token = (home / "token").read_text(encoding="utf-8").strip()
    assert token

    _, status = _api(home, "GET", "/status")
    assert token not in json.dumps(status, ensure_ascii=False)
    assert status["agents"][0]["command"] == "dummy_worker --role dev"

    run_info = json.loads((home / "run" / "dev.json").read_text(encoding="utf-8"))
    assert "ORCHESTRATOR_TOKEN" in run_info["env_keys"]
    assert "SENTINEL_CANARY" not in run_info["env_keys"]

    for log_path in list((home / "logs").glob("*.log")) + list(tmp_path.glob("daemon-*.log")):
        assert token not in log_path.read_text(encoding="utf-8", errors="replace")
