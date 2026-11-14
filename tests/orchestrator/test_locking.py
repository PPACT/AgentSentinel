"""锁文件防死锁用例：真进程持锁、强杀释放、并发抢锁、句柄占用。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from tools.orchestrator.launcher import hidden_creationflags
from tools.orchestrator.locking import (
    FileLock,
    lock_file_name,
    parse_lock_file_name,
    probe,
    unlink_with_retry,
)

ROOT = Path(__file__).resolve().parents[2]

_CHILD = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tools.orchestrator.locking import FileLock
lock = FileLock(Path(sys.argv[2]))
sys.stdin.readline()
ok = lock.acquire(wait_ms=float(sys.argv[3]))
print("ok" if ok else "busy", flush=True)
if ok:
    time.sleep(float(sys.argv[4]))
"""


def _spawn(path: Path, *, wait_ms: float = 0.0, hold_seconds: float = 5.0) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(ROOT), str(path), str(wait_ms), str(hold_seconds)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
        cwd=ROOT,
        creationflags=hidden_creationflags(),
    )


def _release_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def _start_holding(path: Path) -> subprocess.Popen[str]:
    process = _spawn(path)
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write("go\n")
    process.stdin.flush()
    assert process.stdout.readline().strip() == "ok"
    return process


def test_probe_does_not_wait_for_live_holder(tmp_path: Path) -> None:
    path = tmp_path / "dev.lock"
    holder = _start_holding(path)
    try:
        started = time.monotonic()
        assert probe(path) == "busy"
        assert time.monotonic() - started < 0.2
    finally:
        _release_process(holder)


def test_lock_is_released_when_holder_is_killed(tmp_path: Path) -> None:
    path = tmp_path / "dev.lock"
    holder = _start_holding(path)
    holder.kill()
    holder.wait(timeout=10)

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and probe(path) != "free":
        time.sleep(0.02)
    assert probe(path) == "free"


def test_wait_is_capped_at_200ms(tmp_path: Path) -> None:
    path = tmp_path / "dev.lock"
    holder = _start_holding(path)
    try:
        second = FileLock(path)
        started = time.monotonic()
        assert second.acquire(wait_ms=5000) is False
        elapsed = time.monotonic() - started
        assert 0.15 <= elapsed < 1.0
        second.release()
    finally:
        _release_process(holder)


def test_concurrent_acquire_has_single_winner(tmp_path: Path) -> None:
    path = tmp_path / "contended.lock"
    children = [_spawn(path, hold_seconds=1.5) for _ in range(4)]
    try:
        for child in children:
            assert child.stdin is not None
            child.stdin.write("go\n")
            child.stdin.flush()
        results: list[str] = []
        for child in children:
            stdout, stderr = child.communicate(timeout=15)
            assert child.returncode == 0, stderr
            results.append(stdout.strip())
        assert results.count("ok") == 1, results
        assert results.count("busy") == 3, results
    finally:
        for child in children:
            if child.poll() is None:
                _release_process(child)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 共享删除语义")
def test_busy_lock_survives_unlink_attempts(tmp_path: Path) -> None:
    path = tmp_path / "dev.lock"
    holder = _start_holding(path)
    try:
        assert unlink_with_retry(path) is False
        assert path.exists()
        assert probe(path) == "busy"
    finally:
        _release_process(holder)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 共享删除语义")
def test_stale_lock_is_deleted_after_holder_exits(tmp_path: Path) -> None:
    path = tmp_path / "dev.lock"
    holder = _start_holding(path)
    holder.kill()
    holder.wait(timeout=10)

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and probe(path) != "free":
        time.sleep(0.02)
    assert unlink_with_retry(path) is True
    assert not path.exists()


def test_probe_missing_file_is_free(tmp_path: Path) -> None:
    assert probe(tmp_path / "missing.lock") == "free"


def test_lock_file_name_roundtrip() -> None:
    name = lock_file_name("Dev", 1234, 1_700_000_000.9)
    assert name == "dev_1234_1700000000.lock"
    assert parse_lock_file_name(name) == ("dev", 1234, 1_700_000_000.0)
    assert parse_lock_file_name("dev_role_42_1700000000.lock") == (
        "dev_role",
        42,
        1_700_000_000.0,
    )


def test_lock_file_name_rejects_invalid_parts() -> None:
    for role, pid, created in (
        ("", 1, 1.0),
        ("bad role", 1, 1.0),
        ("dev", 0, 1.0),
        ("dev", 1, 0.0),
    ):
        with pytest.raises(ValueError):
            lock_file_name(role, pid, created)
    assert parse_lock_file_name("dev_0_1.lock") is None
    assert parse_lock_file_name("dev_1_bad.lock") is None
    assert parse_lock_file_name("dev-only.json") is None
