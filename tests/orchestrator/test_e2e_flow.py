"""P4 E2E：模拟 doc-agent 发 /need，调度台自动拉起 dev，并验证任务结束后无残留进程。

默认用 dummy worker（快速、零成本）。真实 ``claude -p`` worker 用例需显式设置
``SENTINEL_ORCH_REAL_SMOKE=1``，会产生 token 成本。
"""

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
from tools.orchestrator.launcher import hidden_creationflags, process_alive

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")

ROOT = Path(__file__).resolve().parents[2]


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
        with urllib.request.urlopen(request, timeout=60) as response:
            return int(response.status), _decode(response.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code), _decode(exc.read())


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


def _start_daemon(home: Path, log_path: Path, **env: str) -> DaemonProcess:
    home.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(home)
    environment["PYTHONUTF8"] = "1"
    environment.update(env)
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
            creationflags=hidden_creationflags(),
        )
    daemon = DaemonProcess(home, process)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _read_info(home) is not None:
            try:
                status, body = _api(home, "GET", "/health")
            except OSError:
                pass
            else:
                if status == 200 and body.get("ok"):
                    return daemon
        time.sleep(0.1)
    daemon.stop()
    raise AssertionError("daemon did not become healthy")


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[Path]:
    process = _start_daemon(tmp_path / "orch", tmp_path / "daemon.log")
    try:
        yield process.home
    finally:
        process.stop()


def _wait_task(home: Path, task_id: str, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        code, body = _api(home, "POST", "/task", {"task_id": task_id})
        assert code == 200, body
        if body["task"]["status"] in {"done", "failed", "timeout"}:
            return body
        time.sleep(0.1)
    raise AssertionError(f"task {task_id} not terminal: {body}")


def test_doc_need_auto_starts_dev_and_no_residual(daemon: Path) -> None:
    # 1) doc-agent 发送 /need；调度台自动拉起 dev（dummy worker）
    code, body = _api(
        daemon,
        "POST",
        "/need",
        {
            "from_role": "doc",
            "to_role": "dev",
            "intent": "implement",
            "pointer": "docs/local/交流区/交流区.md#p4",
            "acceptance": ["tests pass"],
            "idempotency_key": "e2e-flow",
        },
    )
    assert code == 200, body
    task_id = body["task"]["task_id"]
    assert body["task"]["status"] == "delivered"

    # 2) 调度台自动拉起的 dev worker 已在 registry 中
    _, status = _api(daemon, "GET", "/status")
    agent = next(item for item in status["agents"] if item["role"] == "dev")
    assert agent["alive"] is True
    assert agent["pid"] > 0
    run_info = json.loads((daemon / "run" / "dev.json").read_text(encoding="utf-8"))
    child_pid = int(run_info["child_pid"])
    assert process_alive(int(agent["pid"])) is True
    assert process_alive(child_pid) is True

    # 3) dev 显式 ACK（带 attempts）→ running
    code, acked = _api(
        daemon,
        "POST",
        "/ack",
        {"task_id": task_id, "role": "dev", "attempts": 1},
    )
    assert code == 200, acked
    assert acked["task"]["status"] == "running"

    # 4) dev 完成 → done；daemon 回收 worker 及其子进程
    code, completed = _api(
        daemon,
        "POST",
        "/completed",
        {
            "task_id": task_id,
            "role": "dev",
            "artifacts": ["workspace/e2e-output.txt"],
            "cost_tokens": 0,
        },
    )
    assert code == 200, completed
    assert completed["task"]["status"] == "done"
    detail = _wait_task(daemon, task_id)
    assert [message["type"] for message in detail["messages"]] == [
        "need",
        "ack",
        "completed",
    ]

    # 5) 无残留：worker + 子进程均 alive=false，registry 置 stopped
    assert process_alive(int(agent["pid"])) is False
    assert process_alive(child_pid) is False
    _, status = _api(daemon, "GET", "/status")
    agent_after = next(item for item in status["agents"] if item["role"] == "dev")
    assert agent_after["status"] == "stopped"
    assert agent_after["alive"] is False


@pytest.mark.skipif(
    os.environ.get("SENTINEL_ORCH_REAL_SMOKE") != "1",
    reason="real claude worker smoke costs tokens; set SENTINEL_ORCH_REAL_SMOKE=1",
)
def test_real_worker_end_to_end(tmp_path: Path) -> None:
    workspace = ROOT / "workspace"
    workspace.mkdir(exist_ok=True)
    pointer = workspace / "e2e-real-pointer.md"
    output = workspace / "e2e-real-output.txt"
    pointer.write_text(
        "Write the exact text P4_E2E to workspace/e2e-real-output.txt using the Write tool.\n"
        "Then call mcp__sentinel__orchestrator_completed with role dev and that artifact.\n",
        encoding="utf-8",
    )
    output.unlink(missing_ok=True)
    daemon = _start_daemon(
        tmp_path / "orch-real",
        tmp_path / "daemon-real.log",
        SENTINEL_ORCH_WORKER_MODE="real",
        SENTINEL_ORCH_WORKER_TIMEOUT_SECONDS="600",
        SENTINEL_ORCH_WATCHDOG_SECONDS="5",
    )
    try:
        code, body = _api(
            daemon.home,
            "POST",
            "/need",
            {
                "from_role": "doc",
                "to_role": "dev",
                "intent": "implement",
                "pointer": str(pointer.relative_to(ROOT)),
                "acceptance": ["workspace/e2e-real-output.txt == P4_E2E"],
                "idempotency_key": "e2e-real",
            },
        )
        assert code == 200, body
        task_id = body["task"]["task_id"]
        _, status = _api(daemon.home, "GET", "/status")
        pid = int(next(item for item in status["agents"] if item["role"] == "dev")["pid"])

        detail = _wait_task(daemon.home, task_id, timeout=180.0)

        assert detail["task"]["status"] == "done"
        assert output.read_text(encoding="utf-8").strip() == "P4_E2E"
        assert [message["type"] for message in detail["messages"]] == [
            "need",
            "ack",
            "completed",
        ]
        # 真实 worker 在 completed 后自然退出；等看护线程记录 usage 并回收
        deadline = time.monotonic() + 60
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert process_alive(pid) is False
        _, usage = _api(daemon.home, "GET", f"/usage?task={task_id}")
        assert usage["usage"]
    finally:
        daemon.stop()
        output.unlink(missing_ok=True)
        pointer.unlink(missing_ok=True)
