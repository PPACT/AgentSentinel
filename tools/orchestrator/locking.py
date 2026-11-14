"""跨进程锁文件：用操作系统锁表示“Agent 进程存活”，零网络心跳。

Windows 使用 ``msvcrt.locking`` 字节范围锁（mandatory），POSIX 使用
``fcntl.flock``（advisory）。锁绑定在打开的文件句柄上，进程退出、崩溃或被
强杀时由操作系统自动释放；磁盘上残留的 ``.lock`` 文件本身不代表锁被持有。

防死锁约束（本模块强制）：
- 获取锁一律非阻塞（``LK_NBLCK`` / ``LOCK_NB``）；只有显式传入 ``wait_ms``
  才允许短暂重试，且上限 200ms。
- 锁区域固定为文件偏移 0 处的 1 个字节。元数据放在文件名里，扫描方无需读取
  锁内容，避免 Windows 强制锁下“读被锁字节”失败。

文件名格式：``<role>_<pid>_<create_time>.lock``。
"""

from __future__ import annotations

import errno
import os
import re
import time
from pathlib import Path

LOCK_SUFFIX = ".lock"
LOCK_BYTES = 1
MAX_WAIT_MS = 200.0
RETRY_INTERVAL_SECONDS = 0.01
UNLINK_ATTEMPTS = 3
UNLINK_RETRY_SECONDS = 0.02
_ROLE_PATTERN = re.compile(r"^[a-z0-9_-]{1,64}$")

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _is_lock_busy(exc: OSError) -> bool:
    """区分“锁被占用”和真正的权限/IO 错误，后者必须向上抛出。"""
    if os.name == "nt":
        winerror = getattr(exc, "winerror", None)
        if winerror == 5:  # ERROR_ACCESS_DENIED：权限不足，不是锁冲突
            return False
        return winerror == 33 or exc.errno in (errno.EACCES, errno.EDEADLK)
    return exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK)


class FileLock:
    """持有单个锁文件的独占锁；``release`` 前句柄始终打开。"""

    def __init__(self, path: Path, *, create: bool = True) -> None:
        self.path = path
        self._create = create
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self, wait_ms: float = 0.0) -> bool:
        """非阻塞抢锁；``wait_ms`` 最多 200ms，超时立即放弃。"""
        if self._fd is not None:
            return True
        fd = self._open()
        wait_seconds = min(max(wait_ms, 0.0), MAX_WAIT_MS) / 1000.0
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, LOCK_BYTES)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
            except OSError as exc:
                if not _is_lock_busy(exc):
                    os.close(fd)
                    raise
                if time.monotonic() >= deadline:
                    os.close(fd)
                    return False
                time.sleep(RETRY_INTERVAL_SECONDS)
                continue
            self._fd = fd
            return True

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, LOCK_BYTES)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[attr-defined]
        except OSError:
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def _open(self) -> int:
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
        try:
            return os.open(self.path, flags)
        except FileNotFoundError:
            if not self._create:
                raise
            return os.open(self.path, flags | os.O_CREAT, 0o600)


def probe(path: Path) -> str:
    """返回 ``free``（无人持有）、``busy``（有活进程）或 ``error``（打不开）。"""
    lock = FileLock(path, create=False)
    try:
        return "free" if lock.acquire() else "busy"
    except FileNotFoundError:
        return "free"
    except OSError:
        return "error"
    finally:
        lock.release()


def unlink_with_retry(path: Path, attempts: int = UNLINK_ATTEMPTS) -> bool:
    """删除锁文件；杀软/索引器短暂占用时重试，失败即返回 False（不等待）。"""
    for attempt in range(attempts):
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt == attempts - 1:
                return False
            time.sleep(UNLINK_RETRY_SECONDS)
    return False


def lock_file_name(role: str, pid: int, create_time: float) -> str:
    normalized = role.strip().casefold()
    if _ROLE_PATTERN.fullmatch(normalized) is None or pid <= 0 or create_time <= 0:
        raise ValueError("invalid lock file name parts")
    return f"{normalized}_{pid}_{int(create_time)}{LOCK_SUFFIX}"


def parse_lock_file_name(name: str) -> tuple[str, int, float] | None:
    if not name.endswith(LOCK_SUFFIX):
        return None
    parts = name[: -len(LOCK_SUFFIX)].rsplit("_", 2)
    if len(parts) != 3:
        return None
    role, pid_text, created_text = parts
    if _ROLE_PATTERN.fullmatch(role) is None:
        return None
    try:
        pid = int(pid_text)
        created = float(created_text)
    except ValueError:
        return None
    if pid <= 0 or created <= 0:
        return None
    return role, pid, created
