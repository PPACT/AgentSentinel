"""P0-1：验证 ``claude -p`` 能以独立进程启动并注入角色提示/环境。

用法：``uv run python tools/orchestrator/probes/p0_claude_print.py``
证据：``.claude/orchestrator/p0/claude_print.json``
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = ROOT / ".claude" / "orchestrator" / "p0"
PROMPT = "Reply with exactly P0_OK and nothing else."
TIMEOUT_SECONDS = 240.0
MAX_BUDGET_USD = "0.10"


def _tail(text: str, limit: int = 4000) -> str:
    return text[-limit:]


def _version() -> str:
    result = subprocess.run(
        ["claude", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    return (result.stdout or result.stderr).strip()


def _command() -> list[str]:
    return [
        "claude",
        "-p",
        PROMPT,
        "--output-format",
        "json",
        "--tools",
        "",
        "--permission-mode",
        "manual",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--max-budget-usd",
        MAX_BUDGET_USD,
        "--append-system-prompt",
        "You are a P0 probe worker. Use no tools.",
    ]


def run() -> dict[str, Any]:
    env = os.environ.copy()
    env["SENTINEL_ROLE"] = "p0-probe"
    started = time.monotonic()
    try:
        completed = subprocess.run(
            _command(),
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "probe": "claude_print",
            "passed": False,
            "version": _version(),
            "command": _command(),
            "cwd": str(ROOT),
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
    passed = completed.returncode == 0 and "P0_OK" in (result_text or completed.stdout)
    return {
        "probe": "claude_print",
        "passed": passed,
        "version": _version(),
        "command": _command(),
        "cwd": str(ROOT),
        "env": {"SENTINEL_ROLE": env["SENTINEL_ROLE"]},
        "exit_code": completed.returncode,
        "duration_seconds": round(elapsed, 2),
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
        "parsed_result": result_text,
        "notes": [
            '--tools "" 禁用全部内置工具，worker 无法 Read/Glob/Bash 项目外路径',
            "--permission-mode manual + --permission-prompts none：需授权的工具一律拒绝，不自动放行",
            "--no-session-persistence：不写会话记录，避免污染",
        ],
    }


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    record = run()
    path = EVIDENCE_DIR / "claude_print.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": record["passed"], "evidence": str(path)}, ensure_ascii=False))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
