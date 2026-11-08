"""P6 真实注册 E2E：新 ``claude -p`` 会话通过项目级 MCP 注册 role。

默认跳过（会消耗 token）；设置 ``SENTINEL_ORCH_REGISTER_SMOKE=1`` 运行一次。
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
from tools.orchestrator.launcher import hidden_creationflags

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")

ROOT = Path(__file__).resolve().parents[2]


def _read_info(home: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((home / "daemon.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _api(
    home: Path, method: str, path: str, payload: dict[str, Any] | None = None
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
            raw = response.read()
            return int(response.status), json.loads(raw.decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8") or "{}")


def _wait_healthy(home: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _read_info(home) is not None:
            try:
                status, _body = _api(home, "GET", "/status")
            except OSError:
                pass
            else:
                if status == 200:
                    return
        time.sleep(0.1)
    raise AssertionError("daemon did not become healthy")


@pytest.fixture
def daemon_home(tmp_path: Path) -> Iterator[Path]:
    home = tmp_path / "orch"
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(home)
    environment["PYTHONUTF8"] = "1"
    log_path = tmp_path / "daemon.log"
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
    try:
        _wait_healthy(home)
        yield home
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


@pytest.mark.skipif(
    os.environ.get("SENTINEL_ORCH_REGISTER_SMOKE") != "1",
    reason="real claude registration smoke costs tokens; set SENTINEL_ORCH_REGISTER_SMOKE=1",
)
def test_headless_claude_registers_via_mcp(daemon_home: Path) -> None:
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(daemon_home)
    environment["PYTHONUTF8"] = "1"
    prompt = (
        "Call the mcp__sentinel__orchestrator_register tool exactly once with "
        "role='dev', session='p6-smoke', ref='register-e2e'. "
        "Then reply with exactly REGISTER_OK and nothing else."
    )
    completed = subprocess.run(
        [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--no-session-persistence",
            "--permission-mode",
            "manual",
            "--permission-prompts",
            "none",
            "--allowedTools",
            "mcp__sentinel__orchestrator_register",
            "--max-budget-usd",
            "0.30",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
        creationflags=hidden_creationflags(),
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result.get("is_error") is not True, result

    status, body = _api(daemon_home, "GET", "/status")
    assert status == 200
    agents = [agent for agent in body["agents"] if agent["role"] == "dev"]
    assert agents, body
    agent = agents[0]
    assert agent["command"] == "mcp_server session=p6-smoke ref=register-e2e"
    assert int(agent["pid"]) > 0
    assert int(agent["pid"]) != os.getpid()

    status, body = _api(daemon_home, "POST", "/resolve", {"role": "dev"})
    assert status == 200
    assert body["agent"]["role"] == "dev"
