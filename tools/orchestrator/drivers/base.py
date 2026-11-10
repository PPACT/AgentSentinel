"""嗅探层公共类型与 cmdline 脱敏。

只承载 `pid/ppid/cwd/cmdline/create_time` 等只读元数据；不读取环境变量、
不打开被扫描进程的文件或网络连接。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

CMDLINE_MAX_CHARS = 300
_MASK = "***"

_SECRET_FLAGS = re.compile(
    r"(?i)(--?(?:token|password|passwd|secret|api[-_]?key|access[-_]?key)"
    r"(?:\s*[=:]\s*|\s+))(\S+)"
)
_ENV_SECRETS = re.compile(
    r"(?i)(\b[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY)[A-Z0-9_]*"
    r"\s*=\s*)(\S+)"
)
_BEARER = re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/=-]+)")


def mask_cmdline(cmdline: str) -> str:
    """屏蔽常见密钥参数/环境变量，并截断到 ``CMDLINE_MAX_CHARS``。"""
    masked = _BEARER.sub(lambda match: match.group(1) + _MASK, cmdline)
    masked = _SECRET_FLAGS.sub(lambda match: match.group(1) + _MASK, masked)
    masked = _ENV_SECRETS.sub(lambda match: match.group(1) + _MASK, masked)
    if len(masked) > CMDLINE_MAX_CHARS:
        masked = masked[: CMDLINE_MAX_CHARS - 1] + "…"
    return masked


@dataclass(frozen=True)
class AgentCandidate:
    """driver 返回的候选：已注册 Agent 或未绑定的野生进程。"""

    pid: int
    ppid: int
    cwd: str
    cmdline: str
    create_time: float
    source: str
    role: str = ""
    registered: bool = False
    root: bool = True
    child_pids: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "ppid": self.ppid,
            "cwd": self.cwd,
            "cmdline": self.cmdline,
            "create_time": self.create_time,
            "source": self.source,
            "root": self.root,
            "child_pids": list(self.child_pids),
            "bound_role": self.role or None,
        }


class AgentDriver(Protocol):
    name: str

    def discover(self, project: str, excluded_pids: Iterable[int] = ()) -> list[AgentCandidate]: ...
