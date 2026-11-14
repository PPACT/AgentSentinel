"""Claude Hook 对接：settings 生成、注入参数与 /hooks/claude/notification。"""

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
from tools.orchestrator import service
from tools.orchestrator.claude_hooks import (
    NOTIFICATION_PATH,
    build_settings,
    settings_path,
    write_settings,
)
from tools.orchestrator.engines import adapter_for
from tools.orchestrator.launcher import hidden_creationflags

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="ConPTY on Windows")

ROOT = Path(__file__).resolve().parents[2]
SESSION = "hook-session"
SHELL = "powershell.exe -NoLogo -NoProfile"


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
    *,
    token: bool = True,
    raw: bytes | None = None,
) -> tuple[int, dict[str, Any]]:
    info = _read_info(home)
    assert info is not None
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = "Bearer " + (home / "token").read_text(encoding="utf-8").strip()
    body = raw if raw is not None else (json.dumps(payload).encode("utf-8") if payload else None)
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
            data = json.loads(response.read().decode("utf-8") or "{}")
            return int(response.status), data if isinstance(data, dict) else {}
    except urllib.error.HTTPError as exc:
        data = json.loads(exc.read().decode("utf-8") or "{}")
        return int(exc.code), data if isinstance(data, dict) else {}


def _wait_healthy(home: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _read_info(home) is not None:
            try:
                status, _ = _api(home, "GET", "/health", token=False)
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
    environment["SENTINEL_ORCH_AGENT_CACHE_SECONDS"] = "3600"
    environment["SENTINEL_PTY_COMMAND"] = SHELL
    environment["SENTINEL_PTY_SESSION_ID"] = SESSION
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


def test_build_settings_only_notification_permission_prompt() -> None:
    data = build_settings("127.0.0.1", 12345, "tok")
    notification = data["hooks"]["Notification"][0]
    assert notification["matcher"] == "permission_prompt"
    hook = notification["hooks"][0]
    assert hook["type"] == "http"
    assert hook["url"] == f"http://127.0.0.1:12345{NOTIFICATION_PATH}"
    assert hook["headers"]["Authorization"] == "Bearer tok"
    assert hook["timeout"] == 2
    assert set(data["hooks"]) == {"Notification"}


def test_write_settings_is_idempotent(tmp_path: Path) -> None:
    path = write_settings(tmp_path, "127.0.0.1", 12345, "tok")
    assert path == settings_path(tmp_path).resolve()
    first = path.stat().st_mtime_ns
    write_settings(tmp_path, "127.0.0.1", 12345, "tok")
    assert path.stat().st_mtime_ns == first
    assert "tok" in path.read_text(encoding="utf-8")


def test_claude_engine_gets_settings_args(tmp_path: Path) -> None:
    daemon = service.Daemon(tmp_path / "orch", port=12345)
    args = daemon._claude_hook_args(adapter_for("claude"))
    assert args[0] == "--settings"
    assert Path(args[1]).is_file()
    assert "12345" in Path(args[1]).read_text(encoding="utf-8")
    assert daemon._claude_hook_args(adapter_for("codex")) == []
    built = adapter_for("claude").build_command("claude", "sid", args)
    assert built[-2:] == args


def test_notify_ignored_when_pty_not_running(tmp_path: Path) -> None:
    daemon = service.Daemon(tmp_path / "orch")
    assert daemon.notify_claude_hook(SESSION)["notice"] is None


def test_hook_endpoint_requires_token(daemon_home: Path) -> None:
    status, _body = _api(
        daemon_home,
        "POST",
        NOTIFICATION_PATH,
        {"notification_type": "permission_prompt", "session_id": SESSION},
        token=False,
    )
    assert status == 401


def test_hook_endpoint_rejects_bad_json(daemon_home: Path) -> None:
    status, body = _api(daemon_home, "POST", NOTIFICATION_PATH, raw=b"{not json")
    assert status == 400
    assert "invalid JSON" in str(body.get("error"))


def test_hook_endpoint_rejects_wrong_type(daemon_home: Path) -> None:
    status, body = _api(
        daemon_home,
        "POST",
        NOTIFICATION_PATH,
        {"notification_type": "idle_prompt", "session_id": SESSION},
    )
    assert status == 400
    assert body["error"] == "unsupported notification_type"


def test_hook_endpoint_rejects_session_mismatch(daemon_home: Path) -> None:
    status, body = _api(
        daemon_home,
        "POST",
        NOTIFICATION_PATH,
        {"notification_type": "permission_prompt", "session_id": "other"},
    )
    assert status == 409
    assert body["error"] == "session mismatch"


def test_hook_notice_broadcast_and_dedupe(daemon_home: Path) -> None:
    status, _ = _api(daemon_home, "POST", "/pty/start", {"role": "", "agent_id": ""})
    assert status == 200
    payload = {"notification_type": "permission_prompt", "session_id": SESSION}

    status, body = _api(daemon_home, "POST", NOTIFICATION_PATH, payload)
    assert status == 200
    assert body["deduped"] is False
    notice = body["notice"]
    assert notice["kind"] == "permission_prompt"
    assert notice["session_id"] == SESSION
    assert "权限" in notice["message"]

    status, body = _api(daemon_home, "POST", NOTIFICATION_PATH, payload)
    assert status == 200
    assert body["deduped"] is True

    status, status_body = _api(daemon_home, "GET", "/pty/status")
    assert status == 200
    assert status_body["notice"]["kind"] == "permission_prompt"
