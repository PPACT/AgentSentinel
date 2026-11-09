"""C2：GET /logs/stream —— 回放最近日志、跟随新增行、鉴权与角色校验。"""

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

ROOT = Path(__file__).resolve().parents[2]


def _read_info(home: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((home / "daemon.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _wait_info(home: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _read_info(home) is not None:
            return
        time.sleep(0.05)
    raise AssertionError("daemon did not start")


def _spawn(home: Path) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(home)
    environment["PYTHONUTF8"] = "1"
    return subprocess.Popen(
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
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=hidden_creationflags(),
    )


def _stop(process: subprocess.Popen[str]) -> tuple[str, str]:
    process.terminate()
    try:
        out, err = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate(timeout=15)
    return out, err


def _url(home: Path, path: str) -> str:
    info = _read_info(home)
    assert info is not None
    return f"http://{info['host']}:{info['port']}{path}"


def _token(home: Path) -> str:
    return (home / "token").read_text(encoding="utf-8").strip()


def _open(home: Path, path: str, *, token: bool = True) -> Any:
    headers = {"Authorization": f"Bearer {_token(home)}"} if token else {}
    request = urllib.request.Request(_url(home, path), headers=headers, method="GET")
    return urllib.request.urlopen(request, timeout=15)


def _next_frame(response: Any) -> dict[str, Any]:
    event = ""
    frame_id = 0
    data: list[str] = []
    while True:
        raw = response.readline()
        if not raw:
            raise AssertionError("log stream ended unexpectedly")
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith(":"):
            continue
        if line == "":
            if event:
                return {"id": frame_id, "event": event, "data": json.loads("".join(data) or "{}")}
            continue
        field, _, value = line.partition(":")
        value = value.lstrip(" ")
        if field == "id":
            frame_id = int(value)
        elif field == "event":
            event = value
        elif field == "data":
            data.append(value)


@pytest.fixture()
def daemon(tmp_path: Path) -> Iterator[Path]:
    home = tmp_path / "orch"
    (home / "logs").mkdir(parents=True)
    process = _spawn(home)
    try:
        _wait_info(home)
        yield home
    finally:
        _stop(process)


def test_logs_stream_replays_and_follows(daemon: Path) -> None:
    log_path = daemon / "logs" / "dev.log"
    log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")

    with _open(daemon, "/logs/stream?role=dev&tail=2") as response:
        assert response.status == 200
        first = _next_frame(response)
        second = _next_frame(response)
    assert first["event"] == "log"
    assert first["data"]["role"] == "dev"
    assert first["data"]["line"] == "two"
    assert second["data"]["line"] == "three"

    with _open(daemon, "/logs/stream?role=dev&tail=0") as response:
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write("four\n")
            stream.flush()
        frame = _next_frame(response)
    assert frame["data"]["line"] == "four"


def test_logs_stream_validates_auth_and_role(daemon: Path) -> None:
    with pytest.raises(urllib.error.HTTPError) as unauthorized:
        _open(daemon, "/logs/stream", token=False)
    assert unauthorized.value.code == 401

    with pytest.raises(urllib.error.HTTPError) as invalid:
        _open(daemon, "/logs/stream?role=..%2Fevil")
    assert invalid.value.code == 400
