"""阶段 B：事件驱动 --watch 与 GET /events SSE（红线测试）。"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from tools.orchestrator import service
from tools.orchestrator.launcher import hidden_creationflags

ROOT = Path(__file__).resolve().parents[2]
WATCH_HEADER = "] 已注册 agent"


def _read_info(home: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((home / "daemon.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _wait_info(home: Path, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = _read_info(home)
        if info is not None:
            return info
        time.sleep(0.05)
    raise AssertionError("daemon did not start")


def _spawn(
    home: Path, *extra: str, env_overrides: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(home)
    environment["PYTHONUTF8"] = "1"
    environment.pop(service.DASHBOARD_REFRESH_ENV, None)
    if env_overrides:
        environment.update(env_overrides)
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
            *extra,
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


def _token(home: Path) -> str:
    return (home / "token").read_text(encoding="utf-8").strip()


def _url(home: Path, path: str) -> str:
    info = _read_info(home)
    assert info is not None
    return f"http://{info['host']}:{info['port']}{path}"


def _api(
    home: Path, method: str, path: str, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    headers = {"Authorization": f"Bearer {_token(home)}"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(_url(home, path), data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return int(response.status), _decode(response.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code), _decode(exc.read())


def _decode(raw: bytes) -> dict[str, Any]:
    data = json.loads(raw.decode("utf-8") or "{}")
    return data if isinstance(data, dict) else {}


def _sse_reader(home: Path, sink: queue.Queue[dict[str, Any]], stop: threading.Event) -> None:
    """后台读取 SSE 帧；解析为 dict 投递到 sink。"""
    request = urllib.request.Request(
        _url(home, "/events"),
        headers={"Authorization": f"Bearer {_token(home)}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            event = ""
            event_id = ""
            data: list[str] = []
            while not stop.is_set():
                raw = response.readline()
                if not raw:
                    break
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith(":"):
                    sink.put({"event": "comment", "data": line})
                    continue
                if line == "":
                    if event:
                        sink.put(
                            {
                                "event": event,
                                "id": int(event_id or 0),
                                "data": json.loads("".join(data) or "{}"),
                            }
                        )
                        if event == "shutdown":
                            break
                    event = ""
                    event_id = ""
                    data = []
                    continue
                field, _, value = line.partition(":")
                value = value.lstrip(" ")
                if field == "event":
                    event = value
                elif field == "id":
                    event_id = value
                elif field == "data":
                    data.append(value)
    except OSError as exc:  # 连接被服务端关闭/超时
        sink.put({"event": "error", "data": str(exc)})


def _next_event(
    sink: queue.Queue[dict[str, Any]], name: str, timeout: float = 10.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            frame = sink.get(timeout=max(0.05, deadline - time.monotonic()))
        except queue.Empty:
            break
        if frame.get("event") == name:
            return frame
    raise AssertionError(f"SSE event not received: {name}")


def test_watch_redraws_once_on_register_and_stop(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    process = _spawn(home, "--watch")
    try:
        _wait_info(home)
        time.sleep(0.4)
        assert _api(home, "POST", "/register", {"role": "dev", "pid": os.getpid()})[0] == 200
        time.sleep(0.6)
        assert _api(home, "POST", "/stop", {"role": "dev"})[0] == 200
        time.sleep(0.6)
        assert _api(home, "GET", "/status")[0] == 200  # 只读不触发重绘
        time.sleep(0.4)
    finally:
        out, err = _stop(process)
    assert out.count(WATCH_HEADER) == 2, (out, err)


def test_watch_ignores_heartbeat_by_default(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    process = _spawn(home, "--watch")
    try:
        _wait_info(home)
        time.sleep(0.4)
        _api(home, "POST", "/register", {"role": "dev", "pid": os.getpid()})
        time.sleep(0.5)
        for _ in range(3):
            assert _api(home, "POST", "/heartbeat", {"role": "dev"})[0] == 200
            time.sleep(0.1)
        time.sleep(0.5)
    finally:
        out, err = _stop(process)
    assert out.count(WATCH_HEADER) == 1, (out, err)


def test_watch_show_heartbeat_redraws(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    process = _spawn(home, "--watch", "--show-heartbeat")
    try:
        _wait_info(home)
        time.sleep(0.4)
        _api(home, "POST", "/register", {"role": "dev", "pid": os.getpid()})
        time.sleep(0.5)
        assert _api(home, "POST", "/heartbeat", {"role": "dev"})[0] == 200
        time.sleep(0.6)
    finally:
        out, err = _stop(process)
    assert out.count(WATCH_HEADER) >= 2, (out, err)


def test_sse_snapshot_register_and_shutdown(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    process = _spawn(home, "--no-dashboard")
    sink: queue.Queue[dict[str, Any]] = queue.Queue()
    stop = threading.Event()
    reader = threading.Thread(target=_sse_reader, args=(home, sink, stop), daemon=True)
    try:
        _wait_info(home)
        reader.start()
        snapshot = _next_event(sink, "snapshot")
        assert "agents" in snapshot["data"]
        _api(home, "POST", "/register", {"role": "dev", "pid": os.getpid()})
        update = _next_event(sink, "snapshot")
        roles = [agent["role"] for agent in update["data"]["agents"]]
        assert "dev" in roles
        assert update["id"] > snapshot["id"]
        assert _api(home, "POST", "/heartbeat", {"role": "dev"})[0] == 200
        heartbeat = _next_event(sink, "heartbeat")
        assert heartbeat["data"]["agents"][0]["role"] == "dev"
        assert _api(home, "POST", "/shutdown", {})[0] == 200
        shutdown = _next_event(sink, "shutdown", timeout=10.0)
        assert shutdown["data"] == {"stopping": True}
    finally:
        stop.set()
        reader.join(timeout=10)
        _stop(process)


def test_sse_ping_and_auth(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    process = _spawn(
        home,
        "--no-dashboard",
        env_overrides={service.SSE_PING_ENV: "0.2"},
    )
    sink: queue.Queue[dict[str, Any]] = queue.Queue()
    stop = threading.Event()
    reader = threading.Thread(target=_sse_reader, args=(home, sink, stop), daemon=True)
    try:
        _wait_info(home)
        reader.start()
        _next_event(sink, "comment")
        request = urllib.request.Request(_url(home, "/events"), method="GET")
        try:
            urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:  # pragma: no cover - 无 token 必须被拒
            raise AssertionError("unauthorized /events must return 401")
    finally:
        stop.set()
        reader.join(timeout=10)
        _stop(process)


def test_sse_client_disconnect_keeps_daemon_healthy(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    process = _spawn(home, "--no-dashboard", env_overrides={service.SSE_PING_ENV: "0.2"})
    try:
        _wait_info(home)
        request = urllib.request.Request(
            _url(home, "/events"),
            headers={"Authorization": f"Bearer {_token(home)}"},
            method="GET",
        )
        response = urllib.request.urlopen(request, timeout=10)
        first_frame = b""
        while True:
            line = response.readline()
            if not line or line in (b"\r\n", b"\n"):
                break
            first_frame += line
        assert b"event: snapshot" in first_frame
        response.close()
        time.sleep(0.5)  # 让服务端 ping/写失败检测到断连
        for _ in range(3):
            assert _api(home, "POST", "/register", {"role": "dev", "pid": os.getpid()})[0] == 200
            time.sleep(0.1)
        assert _api(home, "GET", "/health")[0] == 200
    finally:
        out, err = _stop(process)
    assert "Traceback" not in err, (out, err)


def test_slow_subscriber_keeps_latest_snapshot(tmp_path: Path) -> None:
    daemon = service.Daemon(tmp_path / "orch")
    subscriber = daemon.subscribe()
    daemon.register({"role": "dev", "pid": os.getpid()})
    first = subscriber.get_nowait()
    assert first["event"] == "snapshot"
    daemon.register({"role": "doc", "pid": os.getpid()})
    daemon.register({"role": "ops", "pid": os.getpid()})
    latest = subscriber.get_nowait()
    roles = {agent["role"] for agent in latest["data"]["agents"]}
    assert roles == {"dev", "doc", "ops"}
    assert latest["revision"] == daemon.revision
    assert subscriber.qsize() == 0
    assert daemon.subscribe().maxsize == 1
    daemon.unsubscribe(subscriber)
    daemon.unsubscribe(subscriber)  # 幂等


def test_concurrent_heartbeats_with_sse_and_watch(tmp_path: Path) -> None:
    home = tmp_path / "orch"
    process = _spawn(home, "--watch")
    sink: queue.Queue[dict[str, Any]] = queue.Queue()
    stop = threading.Event()
    reader = threading.Thread(target=_sse_reader, args=(home, sink, stop), daemon=True)
    try:
        _wait_info(home)
        reader.start()
        _next_event(sink, "snapshot")
        _api(home, "POST", "/register", {"role": "dev", "pid": os.getpid()})

        def heartbeat(_: int) -> int:
            return _api(home, "POST", "/heartbeat", {"role": "dev"})[0]

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=5) as pool:
            codes = list(pool.map(heartbeat, range(20)))
        elapsed = time.monotonic() - started
        assert codes == [200] * 20
        assert elapsed < 10.0, elapsed
        assert _api(home, "GET", "/health")[0] == 200
        assert _api(home, "POST", "/shutdown", {})[0] == 200
        _next_event(sink, "shutdown", timeout=10.0)
    finally:
        stop.set()
        reader.join(timeout=10)
        out, err = _stop(process)
    assert "] 已注册 agent" in out, (out, err)


def test_sse_ping_seconds_env(monkeypatch: Any) -> None:
    monkeypatch.delenv(service.SSE_PING_ENV, raising=False)
    assert service.sse_ping_seconds() == 20.0
    monkeypatch.setenv(service.SSE_PING_ENV, "0.2")
    assert service.sse_ping_seconds() == 0.2
    monkeypatch.setenv(service.SSE_PING_ENV, "nope")
    assert service.sse_ping_seconds() == 20.0
