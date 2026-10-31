"""Hook 子进程测试工具：构造 stdin JSON 并断言决策输出。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"


def run_hook(
    script: str,
    payload: dict[str, Any],
    *,
    project_dir: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    process_env["PYTHONUTF8"] = "1"
    process_env.pop("SENTINEL_ROLE", None)
    process_env["CLAUDE_PROJECT_DIR"] = str(project_dir or REPO_ROOT)
    if env is not None:
        process_env.update(env)
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / script)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(project_dir or REPO_ROOT),
        env=process_env,
        check=False,
        timeout=120,
    )


def hook_output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == 0, result.stderr
    text = result.stdout.strip()
    assert text, f"expected hook JSON output, stderr={result.stderr}"
    parsed = json.loads(text)
    output = parsed["hookSpecificOutput"]
    assert isinstance(output, dict)
    return dict(output)
