"""一键交接：唤醒词解析、/clear 后 JSONL 重定位与四步流水线。"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from tools.orchestrator import service
from tools.orchestrator.transcript import newest_session, project_slug

OLD_SESSION = "11111111-1111-1111-1111-111111111111"
NEW_SESSION = "22222222-2222-2222-2222-222222222222"


def test_resolve_wake_prompt_text_file_and_missing(tmp_path: Path) -> None:
    assert (
        service.resolve_wake_prompt({"wake_prompt": "  接续  ", "wake_prompt_file": ""}) == "接续"
    )
    wake_file = tmp_path / "wake.md"
    wake_file.write_text("读取文件里的唤醒词", encoding="utf-8")
    assert (
        service.resolve_wake_prompt({"wake_prompt": "文本", "wake_prompt_file": str(wake_file)})
        == "读取文件里的唤醒词"
    )
    with pytest.raises(ValueError):
        service.resolve_wake_prompt(
            {"wake_prompt": "", "wake_prompt_file": str(tmp_path / "missing.md")}
        )
    assert service.resolve_wake_prompt({"wake_prompt": "", "wake_prompt_file": ""}) == ""


def test_newest_session_after_clear(tmp_path: Path) -> None:
    project = str(tmp_path / "project")
    slug = tmp_path / "config" / "projects" / project_slug(project)
    slug.mkdir(parents=True)
    old = slug / f"{OLD_SESSION}.jsonl"
    old.write_text("{}", encoding="utf-8")
    past = time.time() - 10
    os.utime(old, (past, past))
    new = slug / f"{NEW_SESSION}.jsonl"
    new.write_text("{}", encoding="utf-8")

    assert (
        newest_session(project, after=past + 1, exclude=OLD_SESSION, config_dir=tmp_path / "config")
        == NEW_SESSION
    )
    assert (
        newest_session(project, after=past + 1, exclude=NEW_SESSION, config_dir=tmp_path / "config")
        is None
    )


class _FakePty:
    def __init__(self) -> None:
        self.session_id = OLD_SESSION
        self.writes: list[str] = []
        self.running = True

    def status(self) -> dict[str, object]:
        return {"running": self.running, "session_id": self.session_id}

    def write(self, data: str) -> bool:
        self.writes.append(data)
        return True

    def set_session(self, session_id: str) -> None:
        self.session_id = session_id


def test_handoff_pipeline_injects_four_steps_and_relocates_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTINEL_HANDOFF_PROMPT", "请准备交接")
    monkeypatch.setattr(
        service,
        "read_appended",
        lambda project, session, offset: {
            "messages": [{"role": "assistant", "blocks": [{"kind": "text", "text": "完成"}]}],
            "context_tokens": 0,
            "usage": {},
            "offset": offset + 1,
            "reset": False,
            "bytes": 0,
        },
    )
    monkeypatch.setattr(
        service,
        "newest_session",
        lambda project, *, after, exclude="", config_dir=None: NEW_SESSION,
    )
    daemon = service.Daemon(tmp_path / "orch")
    fake = _FakePty()
    monkeypatch.setattr(daemon, "pty", fake)

    daemon._run_handoff("dev", OLD_SESSION, str(tmp_path), 0, "开始工作\n第二行")

    assert fake.writes[0] == "请准备交接\r"
    assert fake.writes[1] == "/clear\r"
    assert fake.writes[2].startswith("\x1b[200~开始工作")
    assert fake.writes[2].endswith("\x1b[201~\r")
    assert fake.session_id == NEW_SESSION
    assert daemon._pty_sessions["dev"] == NEW_SESSION
    status = daemon.handoff_status()
    assert status["active"] is False
    assert status["phase"] == "done"
    assert status["session_after"] == NEW_SESSION


def test_start_handoff_validations(tmp_path: Path) -> None:
    daemon = service.Daemon(tmp_path / "orch")
    active = daemon.active_workspace()
    assert active["engine"] == "claude"

    # 无唤醒词
    status, body = daemon.start_handoff("dev", "agent")
    assert status == 400 and body["reason"] == "wake_prompt_required"

    # 配置唤醒词但 PTY 未运行
    daemon.workspaces.save({**active, "wake_prompt": "开始工作"})
    status, body = daemon.start_handoff("dev", "agent")
    assert status == 409 and body["reason"] == "pty_not_running"

    # 通用引擎不支持 /clear
    daemon.workspaces.save(
        {
            "name": "codex",
            "project_path": str(tmp_path),
            "command": "codex",
            "engine": "codex",
        },
        activate=True,
    )
    status, body = daemon.start_handoff("dev", "agent")
    assert status == 400 and body["reason"] == "handoff_unsupported"
