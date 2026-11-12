"""只读解析 Claude Code 本地会话 JSONL。

边界：仅允许读取 ``<CLAUDE_CONFIG_DIR>/projects/<项目slug>/<session_id>.jsonl``，
session_id 必须来自本项目 MCP 缓存且通过 UUID 校验；不回显路径、不写回文件。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

DEFAULT_HISTORY_MESSAGES = 200
MAX_HISTORY_MESSAGES = 500
MAX_READ_BYTES = 8 * 1024 * 1024
_SESSION_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


def claude_config_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(override) if override else Path.home() / ".claude"


def project_slug(project: str) -> str:
    # Claude Code 用 '-' 替换路径中的每个非字母数字字符（含 '_'）。
    return re.sub(r"[^A-Za-z0-9]", "-", project.strip())


def transcript_path(
    project: str,
    session_id: str,
    *,
    config_dir: Path | None = None,
) -> Path | None:
    if _SESSION_RE.fullmatch(session_id) is None:
        return None
    base = (config_dir or claude_config_dir()) / "projects"
    candidate = base / project_slug(project) / f"{session_id}.jsonl"
    try:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(base.resolve()) or not resolved.is_file():
            return None
    except OSError:
        return None
    return resolved


def _tool_summary(name: str, data: Any) -> str:
    tool = name.casefold()
    if isinstance(data, dict):
        file = data.get("file_path") or data.get("path")
        if tool == "read" and isinstance(file, str):
            return f"读取 {file}"
        if tool in {"edit", "write", "multiedit"} and isinstance(file, str):
            return file
        pattern = data.get("pattern") or data.get("query")
        if isinstance(pattern, str):
            scope = f" · {data['path']}" if isinstance(data.get("path"), str) else ""
            return f"{pattern}{scope}"
        command = data.get("command")
        if isinstance(command, str):
            return command[:120]
        url = data.get("url")
        if isinstance(url, str):
            return url
        first = next((value for value in data.values() if isinstance(value, str)), "")
        if first:
            return first[:120]
    try:
        return json.dumps(data, ensure_ascii=False)[:160]
    except (TypeError, ValueError):
        return ""


def _blocks(role: str, content: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if isinstance(content, str):
        text = content.strip()
        return [{"kind": "text", "text": text}] if text else []
    if not isinstance(content, list):
        return []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str) and block["text"].strip():
            blocks.append({"kind": "text", "text": block["text"]})
        elif (
            kind == "thinking"
            and isinstance(block.get("thinking"), str)
            and block["thinking"].strip()
        ):
            blocks.append({"kind": "thinking", "text": block["thinking"]})
        elif kind == "tool_use" and role == "assistant":
            name = str(block.get("name", "tool"))
            blocks.append(
                {"kind": "tool", "name": name, "input": _tool_summary(name, block.get("input"))}
            )
        # tool_result 在历史视图中省略，避免大段原始输出。
    return blocks


def parse_entry(entry: Any) -> tuple[dict[str, Any] | None, int]:
    """把一行 transcript 转成 UI 消息；返回 (message, context_tokens)。"""
    if not isinstance(entry, dict) or entry.get("type") not in ("user", "assistant"):
        return None, 0
    if entry.get("isSidechain"):
        return None, 0
    message = entry.get("message")
    if not isinstance(message, dict):
        return None, 0
    role = message.get("role")
    if role not in ("user", "assistant"):
        return None, 0
    blocks = _blocks(str(role), message.get("content"))
    if not blocks:
        return None, 0
    tokens = 0
    if role == "assistant":
        usage = message.get("usage")
        if isinstance(usage, dict):
            tokens = sum(
                int(usage.get(key) or 0)
                for key in (
                    "input_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                )
            )
    return {
        "role": role,
        "blocks": blocks,
        "timestamp": entry.get("timestamp"),
    }, tokens


def read_history(
    project: str,
    session_id: str,
    *,
    limit: int = DEFAULT_HISTORY_MESSAGES,
    config_dir: Path | None = None,
) -> dict[str, Any] | None:
    path = transcript_path(project, session_id, config_dir=config_dir)
    if path is None:
        return None
    try:
        size = path.stat().st_size
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > MAX_READ_BYTES:
        raw = raw[-MAX_READ_BYTES:]
        raw = raw.split(b"\n", 1)[1] if b"\n" in raw else b""

    messages: list[dict[str, Any]] = []
    context_tokens = 0
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message, tokens = parse_entry(entry)
        if message is None:
            continue
        context_tokens = max(context_tokens, tokens)
        messages.append(message)

    bounded = max(1, min(limit, MAX_HISTORY_MESSAGES))
    truncated = len(messages) > bounded
    if truncated:
        messages = messages[-bounded:]
    return {
        "messages": messages,
        "truncated": truncated,
        "context_tokens": context_tokens,
        "bytes": size,
        "offset": size,
    }


def read_appended(
    project: str,
    session_id: str,
    offset: int,
    *,
    config_dir: Path | None = None,
) -> dict[str, Any] | None:
    """从字节 offset 增量读取完整 JSONL 行；半行留到下次。"""
    path = transcript_path(project, session_id, config_dir=config_dir)
    if path is None:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return {"messages": [], "context_tokens": 0, "offset": offset, "reset": False, "bytes": 0}
    reset = size < offset
    start = 0 if reset else offset
    try:
        with path.open("rb") as stream:
            stream.seek(start)
            raw = stream.read()
    except OSError:
        return None
    if b"\n" in raw:
        head, _, _tail = raw.rpartition(b"\n")
        complete = head + b"\n" if head else b""
    else:
        complete = b""
    messages: list[dict[str, Any]] = []
    context_tokens = 0
    for line in complete.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message, tokens = parse_entry(entry)
        if message is None:
            continue
        context_tokens = max(context_tokens, tokens)
        messages.append(message)
    return {
        "messages": messages,
        "context_tokens": context_tokens,
        "offset": start + len(complete),
        "reset": reset,
        "bytes": size,
    }
