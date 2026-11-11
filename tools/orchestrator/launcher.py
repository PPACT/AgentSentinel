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
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil  # type: ignore[import-untyped]  # psutil 未随包提供 stubs

from tools.orchestrator.drivers.cwd_process import ProcessInfo, _psutil_snapshot

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


def hidden_creationflags() -> int:
    """worker / taskkill 不弹控制台窗口；非 Windows 返回 0。"""
    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


# worker 只继承运行所需的系统变量；业务/密钥类变量一律不传。
BASE_ENV_VARS = (
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

# 仅真实 worker 需要：模型认证与 Git Bash 路径；dummy 不继承任何凭证。
REAL_ONLY_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_GIT_BASH_PATH",
    "CLAUDE_CONFIG_DIR",
)

# P2/P3/P4 只允许这些任务上下文进入 worker；token 不写入 registry.command 或日志。
ALLOWED_EXTRA_ENV_VARS = (
    "ROLE",
    "TASK_ID",
    "ORCHESTRATOR_ATTEMPTS",
    "ORCHESTRATOR_URL",
    "ORCHESTRATOR_TOKEN",
    "SENTINEL_ORCH_HEARTBEAT_SECONDS",
    "SENTINEL_ORCH_HOME",
)

MCP_SERVER_NAME = "sentinel"
REAL_ALLOWED_TOOLS = (
    "Read",
    "Glob",
    "Grep",
    "Edit",
    "Write",
    "Bash",
    "mcp__sentinel__orchestrator_status",
    "mcp__sentinel__orchestrator_resolve",
    "mcp__sentinel__orchestrator_need",
    "mcp__sentinel__orchestrator_ack",
    "mcp__sentinel__orchestrator_completed",
    "mcp__sentinel__orchestrator_heartbeat",
    "mcp__sentinel__orchestrator_task",
    "mcp__sentinel__orchestrator_tasks",
)
DEFAULT_WORKER_BUDGET_USD = "0.50"
DEFAULT_WORKER_TIMEOUT_SECONDS = 600.0

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
        # 进程已退出时 OpenProcess 会失败；显式给 alive=false，避免调用方 KeyError。
        return {
            "pid": pid,
            "exists": False,
            "alive": False,
            "exit_code": None,
            "error": ctypes.get_last_error(),
        }
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


WORKER_PROMPT_MARKER = "Active orchestration task:"
_SWEEP_STOP_TIMEOUT_SECONDS = 3.0
_SWEEP_CREATE_TIME_TOLERANCE = 0.05


def _same_path(left: str, right: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
    except (OSError, ValueError):
        return False


def is_leftover_real_worker(info: ProcessInfo, project_root: Path) -> bool:
    """仅认本项目启动器签发的真实 worker，避免误杀用户终端里的 claude -p。"""
    if not _same_path(info.cwd, str(project_root)):
        return False
    if WORKER_PROMPT_MARKER.casefold() not in info.cmdline_text.casefold():
        return False
    tokens = [token.casefold() for token in info.cmdline]
    if not tokens:
        return False
    executable = os.path.basename(tokens[0]).casefold()
    if not executable.startswith("claude"):
        return False
    if "-p" not in tokens and "--print" not in tokens:
        return False
    return "--append-system-prompt" in tokens


def find_leftover_real_workers(
    project_root: Path,
    *,
    snapshot: Callable[[], list[ProcessInfo]] | None = None,
    excluded_pids: Iterable[int] = (),
) -> list[ProcessInfo]:
    list_processes = snapshot if snapshot is not None else _psutil_snapshot
    excluded = {pid for pid in excluded_pids if pid > 0}
    excluded.add(os.getpid())
    return [
        info
        for info in list_processes()
        if info.pid not in excluded and is_leftover_real_worker(info, project_root)
    ]


def kill_process_tree(
    pid: int,
    create_time: float | None = None,
    *,
    process_factory: Callable[[int], Any] | None = None,
) -> list[int]:
    """按 create_time 复核后结束进程树；先子后父，返回实际 kill 的 pid。"""
    if pid <= 0:
        return []
    factory = process_factory or psutil.Process
    try:
        process = factory(pid)
        if create_time is not None and abs(process.create_time() - create_time) > (
            _SWEEP_CREATE_TIME_TOLERANCE
        ):
            return []
        children = process.children(recursive=True)
    except psutil.NoSuchProcess:
        return []
    except (psutil.Error, OSError):
        return []
    targets = [*children, process]
    killed: list[int] = []
    for target in targets:
        try:
            target.terminate()
        except (psutil.Error, OSError):
            continue
        killed.append(int(target.pid))
    _, alive = psutil.wait_procs(targets, timeout=_SWEEP_STOP_TIMEOUT_SECONDS)
    for target in alive:
        try:
            target.kill()
        except (psutil.Error, OSError):
            continue
    return killed


def sweep_leftover_real_workers(
    project_root: Path,
    *,
    snapshot: Callable[[], list[ProcessInfo]] | None = None,
    killer: Callable[[int, float | None], list[int]] | None = None,
    excluded_pids: Iterable[int] = (),
) -> list[int]:
    """daemon 启动时清扫上次崩溃遗留的 ``claude -p`` worker。"""
    kill = killer if killer is not None else kill_process_tree
    killed: list[int] = []
    for info in find_leftover_real_workers(
        project_root, snapshot=snapshot, excluded_pids=excluded_pids
    ):
        killed.extend(kill(info.pid, info.create_time))
    return sorted(set(killed))


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


def worker_environment(
    role: str,
    extra_env: dict[str, str] | None = None,
    *,
    real: bool = False,
) -> dict[str, str]:
    """最小 allowlist；dummy 只拿系统变量，真实 worker 才拿模型认证。"""
    names = BASE_ENV_VARS + (REAL_ONLY_ENV_VARS if real else ())
    environment = {name: os.environ[name] for name in names if name in os.environ}
    environment["SENTINEL_ROLE"] = role
    if extra_env:
        environment.update(
            {key: value for key, value in extra_env.items() if key in ALLOWED_EXTRA_ENV_VARS}
        )
    return environment


def read_role_prompt(role: str) -> str:
    path = PROJECT_ROOT / ".claude" / "agents" / f"{role}-agent.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return f"You are the {role} role."


def worker_budget_usd() -> str:
    return (
        os.environ.get("SENTINEL_ORCH_WORKER_BUDGET_USD", "").strip() or DEFAULT_WORKER_BUDGET_USD
    )


def worker_timeout_seconds() -> float:
    raw = os.environ.get("SENTINEL_ORCH_WORKER_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_WORKER_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_WORKER_TIMEOUT_SECONDS


def real_worker_command(
    role: str,
    *,
    task_id: str,
    pointer: str,
    acceptance: list[str],
    attempts: int,
) -> list[str]:
    """构造真实 ``claude -p`` worker 命令（纯函数，供测试与 spawn_real 共用）。"""
    acceptance_text = json.dumps(acceptance, ensure_ascii=False)
    system_prompt = "\n\n".join(
        [
            read_role_prompt(role),
            f"Active orchestration task: {task_id}",
            f"Pointer: {pointer}",
            f"Acceptance: {acceptance_text}",
            (
                f"FIRST call mcp__{MCP_SERVER_NAME}__orchestrator_ack with "
                f"task_id={task_id!r}, role={role!r}, attempts={attempts}."
            ),
            (
                f"When done call mcp__{MCP_SERVER_NAME}__orchestrator_completed with "
                "task_id, role and artifacts."
            ),
            "Do not start background subagents or use Task/Agent tools.",
        ]
    )
    prompt = (
        f"Work on orchestration task {task_id}. Read the pointer, satisfy acceptance, "
        "ACK first, then COMPLETED."
    )
    return [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--no-session-persistence",
        "--permission-mode",
        "manual",
        "--permission-prompts",
        "none",
        "--allowedTools",
        ",".join(REAL_ALLOWED_TOOLS),
        "--max-budget-usd",
        worker_budget_usd(),
        "--append-system-prompt",
        system_prompt,
    ]


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def parse_claude_usage(log_path: Path) -> dict[str, Any] | None:
    """从 ``claude -p --output-format json`` 的退出日志中解析 usage/cost。"""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        usage = data.get("usage")
        if not isinstance(usage, dict) and "total_cost_usd" not in data:
            continue
        model = ""
        model_usage = data.get("modelUsage")
        if isinstance(model_usage, dict) and model_usage:
            model = str(next(iter(model_usage)))
        usage_map = usage if isinstance(usage, dict) else {}
        cost = data.get("total_cost_usd")
        return {
            "model": model,
            "input_tokens": _as_int(usage_map.get("input_tokens")),
            "output_tokens": _as_int(usage_map.get("output_tokens")),
            "cache_creation_tokens": _as_int(usage_map.get("cache_creation_input_tokens")),
            "cache_read_tokens": _as_int(usage_map.get("cache_read_input_tokens")),
            "cost_usd": float(cost) if isinstance(cost, (int, float)) else 0.0,
            "duration_ms": _as_int(data.get("duration_ms")),
            "status": "error" if data.get("is_error") else "completed",
        }
    return None


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
    task_id: str = ""
    deadline: float = 0.0
    mode: str = "dummy"


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

    def workers(self) -> list[Worker]:
        return list(self._workers.values())

    def spawn_dummy(
        self,
        role: str,
        seconds: float = 120.0,
        extra_env: dict[str, str] | None = None,
    ) -> Worker:
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
                    env=worker_environment(role, extra_env),
                    stdin=subprocess.PIPE,
                    stdout=log_stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=hidden_creationflags(),
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
            mode="dummy",
        )
        self._workers[role] = worker
        return worker

    def spawn_real(
        self,
        role: str,
        *,
        task_id: str,
        pointer: str,
        acceptance: list[str],
        attempts: int,
        orchestrator_url: str,
        token: str,
        orchestrator_home: str = "",
    ) -> Worker:
        """启动真实 ``claude -p`` worker（开发期冒烟用；不接入产品）。"""
        _require_windows()
        job_handle = create_job()
        job_id = uuid.uuid4().hex
        log_path = self.logs_dir / f"{role}-real.log"
        command = real_worker_command(
            role,
            task_id=task_id,
            pointer=pointer,
            acceptance=acceptance,
            attempts=attempts,
        )
        extra_env = {
            "ROLE": role,
            "TASK_ID": task_id,
            "ORCHESTRATOR_ATTEMPTS": str(attempts),
            "ORCHESTRATOR_URL": orchestrator_url,
            "ORCHESTRATOR_TOKEN": token,
        }
        if orchestrator_home:
            extra_env["SENTINEL_ORCH_HOME"] = orchestrator_home
        deadline = time.monotonic() + worker_timeout_seconds()
        process: subprocess.Popen[str] | None = None
        try:
            with log_path.open("a", encoding="utf-8") as stream:
                process = subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT,
                    env=worker_environment(role, extra_env, real=True),
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=hidden_creationflags(),
                )
            if not assign_process(job_handle, process.pid):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        except Exception:
            close_job(job_handle)
            if process is not None:
                process.kill()
            raise
        if process is None:  # pragma: no cover - 防御
            raise RuntimeError("worker process missing")
        pid_file = self.run_dir / f"{role}.json"
        pid_file.write_text(
            json.dumps(
                {"pid": process.pid, "role": role, "task_id": task_id, "mode": "real"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        worker = Worker(
            role=role,
            pid=process.pid,
            pgid=process.pid,
            job_id=job_id,
            child_pid=0,
            job_handle=job_handle,
            process=process,
            pid_file=pid_file,
            log_path=log_path,
            task_id=task_id,
            deadline=deadline,
            mode="real",
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
        worker_state = _wait_dead(worker.pid, KILL_TIMEOUT_SECONDS)
        child_state = _wait_dead(worker.child_pid, KILL_TIMEOUT_SECONDS)
        orphan = bool(worker_state.get("alive") or child_state.get("alive"))
        taskkill_output = ""
        if orphan:
            for target in (worker.pid, worker.child_pid):
                if process_alive(target):
                    completed = subprocess.run(
                        ["taskkill", "/PID", str(target), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        check=False,
                        creationflags=hidden_creationflags(),
                    )
                    taskkill_output += (completed.stdout or "") + (completed.stderr or "")
            worker_state = _wait_dead(worker.pid, KILL_TIMEOUT_SECONDS)
            child_state = _wait_dead(worker.child_pid, KILL_TIMEOUT_SECONDS)
        return {
            "role": role,
            "pid": worker.pid,
            "child_pid": worker.child_pid,
            "job_id": worker.job_id,
            "worker": worker_state,
            "child": child_state,
            "orphan_detected": orphan,
            "taskkill": taskkill_output.strip(),
        }

    def stop_all(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for role in list(self._workers):
            try:
                results.append(self.stop(role))
            except (KeyError, OSError) as exc:
                results.append({"role": role, "error": str(exc)})
        return results
