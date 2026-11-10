"""按 cwd + 进程树发现项目内野生进程。

只调用 ``psutil`` 读取 pid/ppid/cwd/cmdline/create_time；读取失败静默跳过。
发现结果默认只返回进程树根，子进程折叠进 ``child_pids``。
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import psutil  # type: ignore[import-untyped]  # psutil 未随包提供 stubs

from tools.orchestrator.drivers.base import AgentCandidate, mask_cmdline

DEFAULT_CACHE_SECONDS = 2.0

# 基础设施命令行标记：daemon/MCP/Console 及其构建链不应出现在野生列表。
_INFRA_CMDLINE_MARKERS = (
    "tools\\orchestrator\\service.py",
    "tools/orchestrator/service.py",
    "tools\\orchestrator\\mcp_server.py",
    "tools/orchestrator/mcp_server.py",
    "tools.orchestrator.service",
    "tools.orchestrator.mcp_server",
    "agentsentinel-console",
)
_INFRA_TOKEN = re.compile(
    r"(?:^|[\\/\s\"'])(?:vite|cargo|rustc|node_modules)(?:$|[\\/\s\"'])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProcessInfo:
    """进程只读快照；测试可注入伪造值。"""

    pid: int
    ppid: int
    cwd: str
    cmdline: tuple[str, ...]
    create_time: float

    @property
    def cmdline_text(self) -> str:
        return " ".join(self.cmdline)


def _psutil_snapshot() -> list[ProcessInfo]:
    entries: list[ProcessInfo] = []
    for process in psutil.process_iter():
        try:
            entries.append(
                ProcessInfo(
                    pid=process.pid,
                    ppid=process.ppid(),
                    cwd=process.cwd(),
                    cmdline=tuple(process.cmdline()),
                    create_time=process.create_time(),
                )
            )
        except (psutil.Error, OSError):
            # 受保护/已退出/僵尸进程：设计上静默跳过。
            continue
    return entries


def _normalize_path(value: str) -> str:
    if not value.strip():
        return ""
    try:
        return os.path.normcase(os.path.abspath(value))
    except (OSError, ValueError):
        return ""


def _within(path: str, normalized_root: str) -> bool:
    normalized = _normalize_path(path)
    if not normalized or not normalized_root:
        return False
    return normalized == normalized_root or normalized.startswith(normalized_root + os.sep)


def _is_infrastructure(info: ProcessInfo) -> bool:
    text = info.cmdline_text.casefold()
    if any(marker.casefold() in text for marker in _INFRA_CMDLINE_MARKERS):
        return True
    return _INFRA_TOKEN.search(text) is not None


def _descendants(pid: int, children: dict[int, list[int]]) -> set[int]:
    seen: set[int] = set()
    pending = list(children.get(pid, ()))
    while pending:
        current = pending.pop()
        if current in seen or current == pid:
            continue
        seen.add(current)
        pending.extend(children.get(current, ()))
    return seen


def _scan(
    infos: Iterable[ProcessInfo], project: str, excluded_pids: set[int]
) -> list[AgentCandidate]:
    by_pid: dict[int, ProcessInfo] = {}
    for info in infos:
        if info.pid > 0 and info.pid not in by_pid:
            by_pid[info.pid] = info

    children: dict[int, list[int]] = {}
    for info in by_pid.values():
        if info.ppid in by_pid and info.ppid != info.pid:
            children.setdefault(info.ppid, []).append(info.pid)

    blocked: set[int] = set()
    for pid in sorted(excluded_pids):
        if pid in by_pid:
            blocked.add(pid)
            blocked.update(_descendants(pid, children))
    for info in by_pid.values():
        if _is_infrastructure(info):
            blocked.add(info.pid)
            blocked.update(_descendants(info.pid, children))

    def in_project(pid: int) -> bool:
        info = by_pid.get(pid)
        return info is not None and _within(info.cwd, project)

    candidates: list[AgentCandidate] = []
    for pid in sorted(by_pid):
        if pid in blocked or not in_project(pid):
            continue
        info = by_pid[pid]
        parent = by_pid.get(info.ppid)
        if (
            parent is not None
            and parent.pid != pid
            and parent.pid not in blocked
            and in_project(parent.pid)
        ):
            # 会折叠到更上层的项目内 root。
            continue
        descendants = _descendants(pid, children)
        if descendants & blocked:
            # root 子树里含已注册/基础设施进程：视为已注册会话，整棵树隐藏。
            continue
        child_pids = tuple(
            sorted(child for child in descendants if child in by_pid and in_project(child))
        )
        candidates.append(
            AgentCandidate(
                pid=pid,
                ppid=info.ppid,
                cwd=info.cwd,
                cmdline=mask_cmdline(info.cmdline_text),
                create_time=info.create_time,
                source="cwd",
                registered=False,
                root=True,
                child_pids=child_pids,
            )
        )
    candidates.sort(key=lambda candidate: (candidate.create_time, candidate.pid))
    return candidates


class CwdProcessDriver:
    """扫描 cwd 位于项目内的进程；结果缓存 ``cache_seconds`` 秒。"""

    name = "cwd"

    def __init__(
        self,
        snapshot: Callable[[], list[ProcessInfo]] | None = None,
        cache_seconds: float = DEFAULT_CACHE_SECONDS,
    ) -> None:
        self._snapshot = snapshot if snapshot is not None else _psutil_snapshot
        self._cache_seconds = max(0.0, cache_seconds)
        self._lock = threading.Lock()
        self._cache_key: tuple[str, tuple[int, ...]] | None = None
        self._cache_at = 0.0
        self._cache_value: list[AgentCandidate] = []

    def discover(self, project: str, excluded_pids: Iterable[int] = ()) -> list[AgentCandidate]:
        project_key = _normalize_path(project)
        excluded = tuple(sorted({pid for pid in excluded_pids if pid > 0}))
        now = time.monotonic()
        with self._lock:
            if (
                self._cache_seconds > 0
                and self._cache_key == (project_key, excluded)
                and now - self._cache_at < self._cache_seconds
            ):
                return list(self._cache_value)
        try:
            infos = self._snapshot()
        except (psutil.Error, OSError):
            infos = []
        candidates = _scan(infos, project_key, set(excluded))
        with self._lock:
            self._cache_key = (project_key, excluded)
            self._cache_at = now
            self._cache_value = candidates
        return list(candidates)
