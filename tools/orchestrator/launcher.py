"""P1 启动器：Windows Job Object 持有 worker 进程组，提供 spawn / stop / 存活探测。

- Job 设置 ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``：关闭 Job 句柄即回收 worker
  及其所有子孙进程。
- 存活判定只用 ``OpenProcess`` + ``GetExitCodeProcess``（以 ``alive=false`` 为准），
  禁止 ``os.kill(pid, 0)``。
- worker 环境变量走最小 allowlist，禁止 ``os.environ.copy()``。
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DUMMY_WORKER = Path(__file__).with_name("dummy_worker.py")

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259
KILL_TIMEOUT_SECONDS = 10.0
SPAWN_TIMEOUT_SECONDS = 10.0

# worker 只继承运行所需的系统变量；业务/密钥类变量一律不传。
ALLOWED_ENV_VARS = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "USERNAME",
    "USERDOMAIN",
    "HOMEDRIVE",
    "HOMEPATH",
    "COMSPEC",
    "PATHEXT",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
)

_kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _configure_kernel32() -> None:
    if _kernel32 is None:
        return
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


_configure_kernel32()


def _require_windows() -> None:
    if sys.platform != "win32" or _kernel32 is None:
        raise RuntimeError("orchestrator launcher requires Windows")


def process_state(pid: int) -> dict[str, Any]:
    """OpenProcess + GetExitCodeProcess 探测；不向目标发送任何信号。"""
    _require_windows()
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return {"pid": pid, "exists": False, "error": ctypes.get_last_error()}
    try:
        code = wintypes.DWORD()
        ok = _kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return {
            "pid": pid,
            "exists": True,
            "alive": bool(ok and code.value == STILL_ACTIVE),
            "exit_code": code.value if ok else None,
        }
    finally:
        _kernel32.CloseHandle(handle)


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    return bool(process_state(pid).get("alive"))


def create_job() -> int:
    _require_windows()
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    info = _ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    configured = _kernel32.SetInformationJobObject(
        job,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not configured:
        _kernel32.CloseHandle(job)
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
    return int(job)


def assign_process(job_handle: int, pid: int) -> bool:
    _require_windows()
    process_handle = _kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
    if not process_handle:
        return False
    try:
        return bool(_kernel32.AssignProcessToJobObject(job_handle, process_handle))
    finally:
        _kernel32.CloseHandle(process_handle)


def close_job(job_handle: int) -> None:
    _require_windows()
    _kernel32.CloseHandle(job_handle)


def worker_environment(role: str) -> dict[str, str]:
    """最小 allowlist；明确加入 ``SENTINEL_ROLE``，不复制父进程环境。"""
    environment = {name: os.environ[name] for name in ALLOWED_ENV_VARS if name in os.environ}
    environment["SENTINEL_ROLE"] = role
    return environment


def _wait_for_pid_file(path: Path, key: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        else:
            if isinstance(payload, dict) and isinstance(payload.get(key), int):
                return payload
        time.sleep(0.05)
    raise TimeoutError(f"worker pid file {path} missing {key!r}: {last_error}")


def _wait_dead(pid: int, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    state = process_state(pid)
    while state.get("alive") and time.monotonic() < deadline:
        time.sleep(0.1)
        state = process_state(pid)
    return state


@dataclass
class Worker:
    role: str
    pid: int
    pgid: int
    job_id: str
    child_pid: int
    job_handle: int
    process: subprocess.Popen[str]
    pid_file: Path
    log_path: Path


class Launcher:
    def __init__(self, orch_home: Path) -> None:
        self.orch_home = orch_home
        self.logs_dir = orch_home / "logs"
        self.run_dir = orch_home / "run"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._workers: dict[str, Worker] = {}

    def get(self, role: str) -> Worker | None:
        return self._workers.get(role)

    def spawn_dummy(self, role: str, seconds: float = 120.0) -> Worker:
        _require_windows()
        job_handle = create_job()
        job_id = uuid.uuid4().hex
        pid_file = self.run_dir / f"{role}.json"
        pid_file.unlink(missing_ok=True)
        log_path = self.logs_dir / f"{role}.log"
        command = [
            sys.executable,
            str(DUMMY_WORKER),
            "--role",
            role,
            "--pid-file",
            str(pid_file),
            "--seconds",
            str(seconds),
        ]
        process: subprocess.Popen[str] | None = None
        pid = 0
        child_pid = 0
        try:
            with log_path.open("a", encoding="utf-8") as log_stream:
                process = subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT,
                    env=worker_environment(role),
                    stdin=subprocess.PIPE,
                    stdout=log_stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            payload = _wait_for_pid_file(pid_file, "pid", SPAWN_TIMEOUT_SECONDS)
            pid = int(payload["pid"])
            if not assign_process(job_handle, pid):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
            if process.stdin is None:
                raise RuntimeError("worker stdin pipe unavailable")
            process.stdin.write("GO\n")
            process.stdin.flush()
            child_payload = _wait_for_pid_file(pid_file, "child_pid", SPAWN_TIMEOUT_SECONDS)
            child_pid = int(child_payload["child_pid"])
        except Exception:
            close_job(job_handle)
            if process is not None:
                process.kill()
            raise
        if process is None:
            raise RuntimeError("worker process missing")
        worker = Worker(
            role=role,
            pid=pid,
            pgid=pid,
            job_id=job_id,
            child_pid=child_pid,
            job_handle=job_handle,
            process=process,
            pid_file=pid_file,
            log_path=log_path,
        )
        self._workers[role] = worker
        return worker

    def stop(self, role: str) -> dict[str, Any]:
        worker = self._workers.pop(role, None)
        if worker is None:
            raise KeyError(role)
        close_job(worker.job_handle)
        try:
            if worker.process.stdin is not None:
                worker.process.stdin.close()
        except OSError:
            pass
        try:
            worker.process.wait(timeout=KILL_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        return {
            "role": role,
            "pid": worker.pid,
            "child_pid": worker.child_pid,
            "job_id": worker.job_id,
            "worker": _wait_dead(worker.pid, KILL_TIMEOUT_SECONDS),
            "child": _wait_dead(worker.child_pid, KILL_TIMEOUT_SECONDS),
        }

    def stop_all(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for role in list(self._workers):
            try:
                results.append(self.stop(role))
            except (KeyError, OSError) as exc:
                results.append({"role": role, "error": str(exc)})
        return results
