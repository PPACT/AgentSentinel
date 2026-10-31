"""Hook 公共工具：stdin 解析、项目根定位、路径归一化与密钥/PII 扫描。"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SECRET_FIXTURE_MARKER = "# secret-fixture"

# 数据化占位符白名单：明显是示例/占位值时不参与密钥扫描。
WHITELIST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bsk[_-]test[_-]"),
    re.compile(r"(?i)\b(?:x{8,}|0{8,}|\*{6,}|\.{6,})\b"),
    re.compile(r"<[^>\n]{0,80}>"),
    re.compile(r"(?i)\b(?:example|placeholder|dummy|fake)\b"),
)

_ID_CANDIDATE = re.compile(r"(?<!\d)([1-9]\d{16}[\dXx])(?!\d)")
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_CODES = "10X98765432"


def project_root() -> Path:
    override = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    return Path(override).resolve() if override else PROJECT_ROOT


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError("hook payload must be a JSON object")
    return dict(data)


def tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_input")
    return dict(value) if isinstance(value, dict) else {}


def payload_cwd(payload: dict[str, Any]) -> Path:
    raw = payload.get("cwd")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).resolve()
    return project_root()


def relative_project_path(raw: object, payload: dict[str, Any]) -> str | None:
    """把工具入参里的路径归一化为项目内 POSIX 相对路径；项目外返回 None。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = payload_cwd(payload) / path
    try:
        return path.resolve().relative_to(project_root()).as_posix()
    except ValueError:
        return None


def content_texts(tool_in: dict[str, Any]) -> list[str]:
    """收集 Write/Edit/MultiEdit/NotebookEdit 中真正会写入的文本。"""
    texts: list[str] = []
    for key in ("content", "new_string", "new_source"):
        value = tool_in.get(key)
        if isinstance(value, str):
            texts.append(value)
    edits = tool_in.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                value = edit.get("new_string")
                if isinstance(value, str):
                    texts.append(value)
    return texts


def _line_is_placeholder(line: str) -> bool:
    return any(pattern.search(line) for pattern in WHITELIST_PATTERNS)


def _id_checksum_valid(value: str) -> bool:
    digits = value[:17]
    if not digits.isdigit():
        return False
    total = sum(int(digit) * weight for digit, weight in zip(digits, _ID_WEIGHTS, strict=True))
    return value[17].upper() == _ID_CHECK_CODES[total % 11]


def _id_is_synthetic(value: str) -> bool:
    return value[:6] == "999999" or not _id_checksum_valid(value)


def _mask_synthetic_ids(line: str) -> str:
    return _ID_CANDIDATE.sub(
        lambda match: "X" * 18 if _id_is_synthetic(match.group(1)) else match.group(1),
        line,
    )


def scan_secrets(content: str, *, honor_markers: bool = True) -> str | None:
    """返回命中原因（不含密钥明文）；未命中返回 None。

    ``honor_markers`` 为真时，含 ``# secret-fixture`` 的行放行；业务目录应传 False。
    """
    from agentsentinel.contracts import ScanVerdict
    from agentsentinel.governance.sanitizer import RegexSanitizer

    lines: list[str] = []
    for line in content.splitlines():
        if (honor_markers and SECRET_FIXTURE_MARKER in line) or _line_is_placeholder(line):
            lines.append("")
        else:
            lines.append(_mask_synthetic_ids(line))
    result = RegexSanitizer().scan("\n".join(lines))
    if result.verdict is ScanVerdict.DENY:
        return result.reason
    return None


def emit_decision(decision: str, reason: str) -> NoReturn:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    # ASCII 转义避免 Windows cp936 控制台把 UTF-8 输出显示成乱码。
    print(json.dumps(payload))
    raise SystemExit(0)


def emit_context(text: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }
    print(json.dumps(payload))


def fail_closed(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(2)
