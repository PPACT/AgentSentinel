"""本地 transcript 只读解析：过滤元数据、工具摘要、上下文估算与路径边界。"""

from __future__ import annotations

import json
from pathlib import Path

from tools.orchestrator.transcript import project_slug, read_history, transcript_path

PROJECT = r"D:\proj"
SESSION = "11111111-2222-3333-4444-555555555555"


def _write(path: Path, entries: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(item) for item in entries) + "\n", encoding="utf-8")


def _fixture(config: Path) -> Path:
    path = config / "projects" / project_slug(PROJECT) / f"{SESSION}.jsonl"
    _write(
        path,
        [
            {"type": "mode", "sessionId": SESSION},
            {
                "type": "user",
                "isSidechain": False,
                "message": {"role": "user", "content": "你好"},
            },
            {
                "type": "assistant",
                "isSidechain": False,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "想一下"},
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}},
                        {"type": "text", "text": "答案"},
                    ],
                    "usage": {"input_tokens": 100, "cache_read_input_tokens": 900},
                },
            },
            {
                "type": "user",
                "isSidechain": False,
                "message": {"role": "user", "content": [{"type": "tool_result", "content": "raw"}]},
            },
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "subagent"}]},
            },
            {
                "type": "api-request-blob",
                "message": {"role": "user", "content": [{"type": "text", "text": "api payload"}]},
            },
            {
                "type": "assistant",
                "isSidechain": False,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "收尾"}]},
            },
        ],
    )
    return path


def test_project_slug_matches_claude_layout() -> None:
    assert project_slug(r"D:\Code\Work_Code\projects\AgentSentinel") == (
        "D--Code-Work-Code-projects-AgentSentinel"
    )


def test_read_history_filters_and_estimates(tmp_path: Path) -> None:
    _fixture(tmp_path)

    result = read_history(PROJECT, SESSION, config_dir=tmp_path)

    assert result is not None
    roles = [message["role"] for message in result["messages"]]
    assert roles == ["user", "assistant", "assistant"]
    kinds = [block["kind"] for block in result["messages"][1]["blocks"]]
    assert kinds == ["thinking", "tool", "text"]
    assert result["messages"][1]["blocks"][1] == {
        "kind": "tool",
        "name": "Read",
        "input": "读取 a.py",
    }
    assert result["context_tokens"] == 1000
    assert result["truncated"] is False


def test_read_history_limit_and_boundary(tmp_path: Path) -> None:
    _fixture(tmp_path)

    result = read_history(PROJECT, SESSION, limit=2, config_dir=tmp_path)

    assert result is not None
    assert result["truncated"] is True
    assert len(result["messages"]) == 2
    assert transcript_path(PROJECT, "../escape", config_dir=tmp_path) is None
    assert transcript_path(PROJECT, "not-a-uuid", config_dir=tmp_path) is None
    assert read_history(PROJECT, "99999999-2222-3333-4444-555555555555", config_dir=tmp_path) is None
