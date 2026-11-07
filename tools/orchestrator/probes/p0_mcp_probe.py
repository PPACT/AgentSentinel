"""P0-3 驱动：用临时 MCP 配置让 Claude Code 拉起 echo server 并调用工具。

用法：``uv run python tools/orchestrator/probes/p0_mcp_probe.py``
证据：``.claude/orchestrator/p0/mcp_probe.json``
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = ROOT / ".claude" / "orchestrator" / "p0"
SERVER_SCRIPT = Path(__file__).with_name("p0_mcp_echo_server.py")
CONFIG_PATH = EVIDENCE_DIR / "mcp-echo.json"
LOG_PATH = EVIDENCE_DIR / "mcp_server.log"
PROMPT = (
    "Call the p0echo MCP tool with message 'p0-ok', then reply with exactly "
    "the tool result text and nothing else."
)
TIMEOUT_SECONDS = 240.0
MAX_BUDGET_USD = "0.20"


def _tail(text: str, limit: int = 4000) -> str:
    return text[-limit:]


def _write_config() -> None:
    config = {
        "mcpServers": {
            "p0echo": {
                "command": sys.executable,
                "args": [str(SERVER_SCRIPT)],
                "env": {"P0_MCP_LOG": str(LOG_PATH)},
            }
        }
    }
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _command() -> list[str]:
    return [
        "claude",
        "-p",
        PROMPT,
        "--output-format",
        "json",
        "--mcp-config",
        str(CONFIG_PATH),
        "--strict-mcp-config",
        "--tools",
        "",
        "--allowedTools",
        "mcp__p0echo__echo",
        "--permission-mode",
        "manual",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--max-budget-usd",
        MAX_BUDGET_USD,
        "--append-system-prompt",
        "Only use the p0echo MCP tool; do not use any other tool.",
    ]


def run() -> dict[str, Any]:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.unlink(missing_ok=True)
    _write_config()
    started = time.monotonic()
    try:
        completed = subprocess.run(
            _command(),
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "probe": "mcp_stdio",
            "passed": False,
            "command": _command(),
            "error": f"timeout after {TIMEOUT_SECONDS}s",
            "stdout_tail": _tail(str(exc.stdout or "")),
            "stderr_tail": _tail(str(exc.stderr or "")),
        }

    elapsed = time.monotonic() - started
    parsed: Any = None
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        parsed = None
    result_text = ""
    if isinstance(parsed, dict):
        value = parsed.get("result")
        if isinstance(value, str):
            result_text = value
    server_log = ""
    try:
        server_log = LOG_PATH.read_text(encoding="utf-8")
    except OSError:
        server_log = ""
    passed = (
        completed.returncode == 0
        and "p0-ok" in (result_text or completed.stdout)
        and "tools/call" in server_log
    )
    return {
        "probe": "mcp_stdio",
        "passed": passed,
        "command": _command(),
        "cwd": str(ROOT),
        "exit_code": completed.returncode,
        "duration_seconds": round(elapsed, 2),
        "parsed_result": result_text,
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
        "server_log_tail": _tail(server_log, 2000),
        "config_path": str(CONFIG_PATH),
        "notes": [
            "使用临时 MCP 配置 + --strict-mcp-config，未改项目级 .mcp.json",
            "server 用 stdlib 实现 JSON-RPC（initialize / tools/list / tools/call）",
            '--tools "" 只禁用内置工具；MCP 工具由 --allowedTools 精确放行',
        ],
    }


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    record = run()
    path = EVIDENCE_DIR / "mcp_probe.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": record["passed"], "evidence": str(path)}, ensure_ascii=False))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
