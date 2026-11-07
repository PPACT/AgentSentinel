"""P0-2：Windows Job Object ``KILL_ON_JOB_CLOSE`` 回收 worker 及其子进程。

只验证能力，不实现启动器。存活探测统一用 ``OpenProcess`` +
``GetExitCodeProcess``，**不使用** ``os.kill(pid, 0)``。

用法：``uv run python tools/orchestrator/probes/p0_job_object.py``
证据：``.claude/orchestrator/p0/job_object.json``
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = ROOT / ".claude" / "orchestrator" / "p0"
WORKER_SCRIPT = Path(__file__).with_name("p0_dummy_worker.py")

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259
TERMINATE_TIMEOUT_SECONDS = 10.0

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


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


kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.SetInformationJobObject.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def process_state(pid: int) -> dict[str, Any]:
    """用 OpenProcess + GetExitCodeProcess 探测，不发送任何信号。"""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return {"pid": pid, "exists": False, "error": ctypes.get_last_error()}
    try:
        code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return {
            "pid": pid,
            "exists": True,
            "alive": bool(ok and code.value == STILL_ACTIVE),
            "exit_code": code.value if ok else None,
        }
    finally:
        kernel32.CloseHandle(handle)


def _wait_dead(pid: int, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    state = process_state(pid)
    while state.get("alive") and time.monotonic() < deadline:
        time.sleep(0.2)
        state = process_state(pid)
    return state


def _read_pid(stream: Any, marker: str) -> int:
    line = stream.readline().strip()
    if not line.startswith(marker):
        raise RuntimeError(f"unexpected worker output: {line!r}")
    return int(line.split(marker, 1)[1])


def run() -> dict[str, Any]:
    if sys.platform != "win32":
        return {"probe": "job_object", "passed": False, "error": "requires Windows"}

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return {
            "probe": "job_object",
            "passed": False,
            "stage": "CreateJobObjectW",
            "error": ctypes.get_last_error(),
        }

    info = _ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    configured = kernel32.SetInformationJobObject(
        job,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )

    worker = subprocess.Popen(
        [sys.executable, str(WORKER_SCRIPT)],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if worker.stdout is None or worker.stdin is None:
        kernel32.CloseHandle(job)
        return {"probe": "job_object", "passed": False, "error": "failed to open pipes"}

    worker_pid = _read_pid(worker.stdout, "WORKER_PID=")
    process_handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, worker_pid)
    assigned = bool(process_handle and kernel32.AssignProcessToJobObject(job, process_handle))
    assign_error = ctypes.get_last_error()
    if process_handle:
        kernel32.CloseHandle(process_handle)

    worker.stdin.write("spawn\n")
    worker.stdin.flush()
    child_pid = _read_pid(worker.stdout, "CHILD_PID=")

    before = {"worker": process_state(worker_pid), "child": process_state(child_pid)}
    time.sleep(0.5)
    probe_check = {"worker": process_state(worker_pid), "child": process_state(child_pid)}
    probe_alive = process_state(os.getpid())

    kernel32.CloseHandle(job)
    after = {
        "worker": _wait_dead(worker_pid, TERMINATE_TIMEOUT_SECONDS),
        "child": _wait_dead(child_pid, TERMINATE_TIMEOUT_SECONDS),
    }
    try:
        worker.wait(timeout=TERMINATE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        pass

    passed = (
        bool(configured)
        and assigned
        and bool(before["worker"].get("alive"))
        and bool(before["child"].get("alive"))
        and bool(probe_check["worker"].get("alive"))
        and bool(probe_check["child"].get("alive"))
        and bool(probe_alive.get("alive"))
        and not after["worker"].get("alive")
        and not after["child"].get("alive")
    )
    return {
        "probe": "job_object",
        "passed": passed,
        "job_handle_created": True,
        "limit_configured": bool(configured),
        "assign_ok": assigned,
        "assign_error": assign_error,
        "worker_pid": worker_pid,
        "child_pid": child_pid,
        "alive_before_close": before,
        "alive_after_probe": probe_check,
        "probe_process": probe_alive,
        "alive_after_job_close": after,
        "terminate_seconds": TERMINATE_TIMEOUT_SECONDS,
        "notes": [
            "存活探测只用 OpenProcess + GetExitCodeProcess，未使用 os.kill(pid, 0)",
            "子进程在 worker 被加入 Job 之后派生，因此继承 Job 成员身份",
            "关闭 Job 句柄触发 KILL_ON_JOB_CLOSE，worker 与孙进程同时回收",
        ],
    }


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    record = run()
    path = EVIDENCE_DIR / "job_object.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": record["passed"], "evidence": str(path)}, ensure_ascii=False))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
