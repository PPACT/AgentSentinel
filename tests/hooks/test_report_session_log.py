"""Stop Hook：外部会话日志上报到编排 daemon。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

from tests.hooks.hook_utils import run_hook


class _Recorder(BaseHTTPRequestHandler):
    bodies: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        type(self).bodies.append(json.loads(raw or b"{}"))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: Any) -> None:
        return


def _home(tmp_path: Path, port: int) -> Path:
    home = tmp_path / "orch"
    home.mkdir()
    (home / "daemon.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": port}), encoding="utf-8"
    )
    (home / "token").write_text("fixture-token", encoding="utf-8")
    return home


def test_stop_hook_reports_last_assistant_message(tmp_path: Path) -> None:
    _Recorder.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        home = _home(tmp_path, int(server.server_address[1]))
        result = run_hook(
            "report_session_log.py",
            {"hook_event_name": "Stop", "last_assistant_message": "已完成修复\n并跑了测试"},
            env={"SENTINEL_ROLE": "dev", "SENTINEL_ORCH_HOME": str(home)},
        )
        assert result.returncode == 0
        assert _Recorder.bodies == [{"role": "dev", "line": "[turn] 已完成修复 并跑了测试"}]
    finally:
        server.shutdown()
        server.server_close()


def test_stop_hook_does_not_read_transcript(tmp_path: Path) -> None:
    _Recorder.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        home = _home(tmp_path, int(server.server_address[1]))
        transcript = tmp_path / "session.jsonl"
        payload = {"type": "assistant", "message": {"content": [{"type": "text", "text": "不应读取"}]}}
        transcript.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        result = run_hook(
            "report_session_log.py",
            {"hook_event_name": "Stop", "transcript_path": str(transcript)},
            env={"SENTINEL_ROLE": "dev", "SENTINEL_ORCH_HOME": str(home)},
        )
        assert result.returncode == 0
        assert _Recorder.bodies == []
    finally:
        server.shutdown()
        server.server_close()


def test_stop_hook_reports_direct_message_and_truncates(tmp_path: Path) -> None:
    _Recorder.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        home = _home(tmp_path, int(server.server_address[1]))
        result = run_hook(
            "report_session_log.py",
            {"hook_event_name": "Stop", "last_assistant_message": "直接消息\n第二行"},
            env={"SENTINEL_ROLE": "doc", "SENTINEL_ORCH_HOME": str(home)},
        )
        assert result.returncode == 0
        assert _Recorder.bodies == [{"role": "doc", "line": "[turn] 直接消息 第二行"}]

        _Recorder.bodies = []
        result = run_hook(
            "report_session_log.py",
            {"hook_event_name": "Stop", "last_assistant_message": "长" * 5000},
            env={"SENTINEL_ROLE": "doc", "SENTINEL_ORCH_HOME": str(home)},
        )
        assert result.returncode == 0
        assert len(_Recorder.bodies[0]["line"]) == len("[turn] ") + 1200
    finally:
        server.shutdown()
        server.server_close()


def test_stop_hook_silent_without_role_or_daemon(tmp_path: Path) -> None:
    _Recorder.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        home = _home(tmp_path, int(server.server_address[1]))
        result = run_hook(
            "report_session_log.py",
            {"hook_event_name": "Stop", "last_assistant_message": "x"},
            env={"SENTINEL_ROLE": "", "SENTINEL_ORCH_HOME": str(home)},
        )
        assert result.returncode == 0
        assert _Recorder.bodies == []

        result = run_hook(
            "report_session_log.py",
            {"hook_event_name": "Stop", "last_assistant_message": "x"},
            env={"SENTINEL_ROLE": "dev", "SENTINEL_ORCH_HOME": str(tmp_path / "missing")},
        )
        assert result.returncode == 0
        assert _Recorder.bodies == []
    finally:
        server.shutdown()
        server.server_close()
