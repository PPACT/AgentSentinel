"""C1 代理契约测试：静态托管、Bearer 注入、SSE 透传、401/503。

这些用例位于试验目录，默认 ``pytest`` 的 testpaths 不收集；运行：
``uv run pytest experiments/frontend-dashboard/tests``
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
SERVER = EXPERIMENT_DIR / "server.py"
STATIC_DIR = EXPERIMENT_DIR / "static"
TEST_TOKEN = "test-token-c1-only"


class _StubState:
    seen_auth: ClassVar[list[str]] = []


class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self) -> None:
        _StubState.seen_auth.append(self.headers.get("Authorization", ""))
        if self.path == "/status":
            self._send_json(
                200,
                {
                    "daemon": {
                        "pid": 1,
                        "host": "127.0.0.1",
                        "port": 1,
                        "started_at": "t0",
                    },
                    "agents": [{"role": "dev", "alive": True}],
                },
            )
        elif self.path == "/unauthorized":
            self._send_json(401, {"error": "unauthorized"})
        elif self.path.startswith("/events"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b'id: 0\nevent: snapshot\ndata: {"agents": []}\n\n')
            self.wfile.flush()
            self.close_connection = True
        else:
            self._send_json(404, {"error": "not found"})


@dataclass
class Proxy:
    process: subprocess.Popen[str]
    base: str
    upstream: ThreadingHTTPServer | None
    home: Path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(base: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + "/", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.05)
    raise AssertionError("proxy did not start")


def _start_proxy(home: Path, upstream_url: str) -> Proxy:
    upstream: ThreadingHTTPServer | None = None
    if upstream_url.startswith("http://127.0.0.1:0"):
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}"
    port = _free_port()
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(home)
    environment["SENTINEL_ORCH_URL"] = upstream_url
    environment["PYTHONUTF8"] = "1"
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    process = subprocess.Popen(
        [sys.executable, str(SERVER), "--port", str(port)],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    proxy = Proxy(process=process, base=f"http://127.0.0.1:{port}", upstream=upstream, home=home)
    _wait_ready(proxy.base)
    return proxy


def _stop_proxy(proxy: Proxy) -> None:
    proxy.process.terminate()
    try:
        proxy.process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proxy.process.kill()
        proxy.process.communicate(timeout=10)
    if proxy.upstream is not None:
        proxy.upstream.shutdown()
        proxy.upstream.server_close()


@pytest.fixture()
def proxy(tmp_path: Path) -> Iterator[Proxy]:
    _StubState.seen_auth.clear()
    home = tmp_path / "orch"
    home.mkdir()
    (home / "token").write_text(TEST_TOKEN, encoding="utf-8")
    instance = _start_proxy(home, "http://127.0.0.1:0")
    try:
        yield instance
    finally:
        _stop_proxy(instance)


@pytest.fixture()
def down_proxy(tmp_path: Path) -> Iterator[Proxy]:
    home = tmp_path / "orch"
    home.mkdir()
    (home / "token").write_text(TEST_TOKEN, encoding="utf-8")
    instance = _start_proxy(home, f"http://127.0.0.1:{_free_port()}")
    try:
        yield instance
    finally:
        _stop_proxy(instance)


def test_static_index_served_without_token_leak(proxy: Proxy) -> None:
    with urllib.request.urlopen(proxy.base + "/", timeout=5) as response:
        assert response.status == 200
        html = response.read().decode("utf-8")
    assert "AgentSentinel 中控" in html
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in STATIC_DIR.rglob("*") if path.is_file()
    )
    assert TEST_TOKEN not in source
    assert "setInterval" not in source
    assert "new EventSource" not in source


def test_proxy_status_injects_bearer(proxy: Proxy) -> None:
    with urllib.request.urlopen(proxy.base + "/api/status", timeout=5) as response:
        assert response.status == 200
        payload = json.loads(response.read().decode("utf-8"))
    assert payload["daemon"]["pid"] == 1
    assert "Bearer " + TEST_TOKEN in _StubState.seen_auth


def test_proxy_events_streams_sse(proxy: Proxy) -> None:
    with urllib.request.urlopen(proxy.base + "/api/events", timeout=5) as response:
        assert response.status == 200
        body = response.read().decode("utf-8")
    assert "event: snapshot" in body
    assert "Bearer " + TEST_TOKEN in _StubState.seen_auth


def test_proxy_passes_401(proxy: Proxy) -> None:
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(proxy.base + "/api/unauthorized", timeout=5)
    assert excinfo.value.code == 401


def test_daemon_down_returns_503(down_proxy: Proxy) -> None:
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(down_proxy.base + "/api/status", timeout=5)
    assert excinfo.value.code == 503
    payload = json.loads(excinfo.value.read().decode("utf-8"))
    assert payload["error"] == "中控未启动"
