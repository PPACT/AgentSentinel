"""编码与断连韧性：daemon 启动环境、stdio 替换、HTTP/SSE 写失败静默处理。"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, cast

import pytest
from tools.orchestrator import cli, service


class _ResetWriter:
    """模拟客户端断开后的 wfile：任何写入都抛 WinError 10054。"""

    def write(self, _data: bytes) -> int:
        raise ConnectionResetError(10054, "connection reset")

    def flush(self) -> None:
        raise ConnectionResetError(10054, "connection reset")


class _RecordingStream:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def reconfigure(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _BrokenStream:
    def reconfigure(self, **kwargs: Any) -> None:
        raise ValueError("stream already closed")


class _HeaderResetHandler:
    """模拟 send_response/end_headers 阶段对端已断开。"""

    def __init__(self) -> None:
        self.wfile = _ResetWriter()

    def send_response(self, status: int) -> None:
        return None

    def send_header(self, keyword: str, value: str) -> None:
        return None

    def end_headers(self) -> None:
        raise ConnectionResetError(10054, "connection reset")


def _handler_with(stream: Any) -> service._Handler:
    handler = object.__new__(service._Handler)
    handler.wfile = stream
    return handler


def test_configure_stdio_uses_replace_and_tolerates_odd_streams() -> None:
    recording = _RecordingStream()
    service._configure_stdio(recording, _BrokenStream(), object())
    assert recording.calls == [{"errors": "replace"}]


def test_sse_writes_return_false_on_connection_reset() -> None:
    handler = _handler_with(_ResetWriter())
    assert handler._write_bytes(b": ping\n\n") is False
    assert handler._write_event(1, "snapshot", {"ok": True}) is False
    assert handler._write_log_frame(1, "dev", "line") is False


def test_send_swallows_connection_reset() -> None:
    service._Handler._send(cast(Any, _HeaderResetHandler()), 200, {"ok": True})


def test_decode_log_line_handles_legacy_gbk() -> None:
    text = "远程主机强迫关闭了一个现有的连接。"
    assert service._decode_log_line(text.encode()) == text
    assert service._decode_log_line(text.encode("gbk")) == text
    assert "�" in service._decode_log_line(b"\xff\xfe")


def test_split_log_chunk_handles_mixed_encoding_and_partial_bytes() -> None:
    legacy = "旧日志\n".encode("gbk")
    current = "新日志\n".encode()
    lines, rest = service._split_log_chunk(b"", legacy + current)
    assert lines == ["旧日志", "新日志"]
    assert rest == b""

    payload = "中文".encode()
    lines, rest = service._split_log_chunk(b"", payload[:2])
    assert lines == []
    assert rest == payload[:2]
    lines, rest = service._split_log_chunk(rest, payload[2:] + b"\n")
    assert lines == ["中文"]
    assert rest == b""


def test_tail_lines_decodes_legacy_gbk(tmp_path: Path) -> None:
    log = tmp_path / "daemon.log"
    log.write_bytes("旧日志\n".encode("gbk") + "新日志\n".encode())
    assert service._tail_lines(log, 10) == ["旧日志", "新日志"]


def test_daemon_start_forces_utf8_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    class _FakePopen:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(cast(dict[str, str], kwargs.get("env", {})))

    infos = iter([None, {"host": "127.0.0.1", "port": 8765, "pid": 1}])
    # 首次 info 为 None 时短路，不会调用 _healthy；进入等待循环后才调用一次。
    health = iter([True])

    def fake_info(home: Path) -> dict[str, Any] | None:
        return next(infos)

    def fake_healthy(home: Path) -> bool:
        return next(health)

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(cli, "_read_daemon_info", fake_info)
    monkeypatch.setattr(cli, "_healthy", fake_healthy)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    home = tmp_path / "orch"
    result = cli._daemon_start(home, 8765)

    assert result["running"] is True
    assert captured["PYTHONUTF8"] == "1"
    assert captured["PYTHONIOENCODING"] == "utf-8"
    assert captured["SENTINEL_ORCH_HOME"] == str(home)
