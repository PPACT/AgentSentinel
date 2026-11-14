"""Claude Code Hook 注入文件：只生成 Notification(permission_prompt) 回调配置。

文件落在 daemon home 的 ``claude-hooks/`` 下（与 daemon token 同目录，均不进仓库），
通过 ``claude --settings <绝对路径>`` 注入，叠加在用户/项目 settings 之上。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

NOTIFICATION_PATH = "/hooks/claude/notification"
PERMISSION_MATCHER = "permission_prompt"
SETTINGS_DIR = "claude-hooks"
SETTINGS_FILE = "settings.json"
HOOK_TIMEOUT_SECONDS = 2


def settings_path(home: Path) -> Path:
    return home / SETTINGS_DIR / SETTINGS_FILE


def build_settings(host: str, port: int, token: str) -> dict[str, Any]:
    """最小配置：仅 Notification，matcher 固定 permission_prompt。"""
    return {
        "hooks": {
            "Notification": [
                {
                    "matcher": PERMISSION_MATCHER,
                    "hooks": [
                        {
                            "type": "http",
                            "url": f"http://{host}:{port}{NOTIFICATION_PATH}",
                            "headers": {"Authorization": f"Bearer {token}"},
                            "timeout": HOOK_TIMEOUT_SECONDS,
                        }
                    ],
                }
            ]
        }
    }


def write_settings(home: Path, host: str, port: int, token: str) -> Path:
    """原子写入（内容不变则不重写），返回绝对路径。"""
    path = settings_path(home)
    payload = json.dumps(build_settings(host, port, token), ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_text(encoding="utf-8") == payload:
            return path.resolve()
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(tmp, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path.resolve()
