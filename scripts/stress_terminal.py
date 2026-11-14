"""终端高压测试：WebView2 CPU/GPU 基线 vs 终端可见 vs 对话页隐藏，自动切 Tab 与注入。

用法：
  .venv/Scripts/python.exe scripts/stress_terminal.py --raw-shell
前置：当前激活工作区的 PTY 是**原生 shell**（如 powershell），本脚本直接向其注入
`python scripts/flood_stdout.py 65`。Claude Code 引擎的 PTY 不要用本脚本压测：
`!` shell 模式会捕获子进程输出，不会形成原始洪流（实测 ~200 B/s）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Self

import psutil  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / ".claude" / "orchestrator"
APP_EXE = "agentsentinel-console.exe"
FLOOD = ROOT / "scripts" / "flood_stdout.py"
MARKER = ROOT / ".claude" / "flood.started"
FLOOD_SECONDS = 65
BASELINE_SECONDS = 10.0
SCENARIO_SECONDS = 20.0


def app_pid() -> int:
    for process in psutil.process_iter(["pid", "name"]):
        if (process.info["name"] or "").lower() == APP_EXE:
            return int(process.info["pid"])
    raise SystemExit(f"{APP_EXE} 未运行")


def webview_pids(pid: int) -> list[int]:
    root = psutil.Process(pid)
    return [
        child.pid
        for child in root.children(recursive=True)
        if (child.name() or "").lower() == "msedgewebview2.exe"
    ]


def restore_window(pid: int) -> None:
    """恢复并前置工作台窗口：最小化会暂停 WebView2 渲染，采集数据无效。"""
    script = f"""
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class StressWin {{
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}}
'@
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$cond=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty,{pid})
$wins=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,$cond)
foreach($w in $wins){{
  if($w.Current.ClassName -eq 'Tauri Window'){{
    $h=[IntPtr]$w.Current.NativeWindowHandle
    [StressWin]::ShowWindow($h,9) | Out-Null
    [StressWin]::SetForegroundWindow($h) | Out-Null
    'restored'
  }}
}}
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def select_tab(pid: int, index: int) -> bool:
    script = f"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$cond=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty,{pid})
$wins=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,$cond)
$tabs=@()
foreach($w in $wins){{ $all=$w.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition); foreach($e in $all){{ if($e.Current.ControlType.ProgrammaticName -eq 'ControlType.TabItem'){{ $tabs += $e }} }} }}
if($tabs.Count -lt 3){{ 'ERR tabs=' + $tabs.Count; exit 1 }}
$tabs[{index}].GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
Start-Sleep -Milliseconds 400
$sel=@(); foreach($t in $tabs){{ $sel += $t.GetCurrentPropertyValue([System.Windows.Automation.SelectionItemPattern]::IsSelectedProperty) }}
($sel -join ',')
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.returncode == 0


def _api(
    method: str, path: str, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    info = json.loads((HOME / "daemon.json").read_text(encoding="utf-8"))
    token = (HOME / "token").read_text(encoding="utf-8").strip()
    headers = {"Authorization": f"Bearer {token}"}
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://{info['host']}:{info['port']}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return int(response.status), json.loads(response.read().decode("utf-8") or "{}")


def inject_flood(raw_shell: bool) -> bool:
    status, body = _api("GET", "/pty/status")
    if status != 200 or not body.get("running"):
        raise SystemExit(f"PTY 未运行：{status} {body}")
    # Windows 路径用正斜杠，避免被 bash 风格的 shell 吞掉反斜杠。
    python = sys.executable.replace("\\", "/")
    command = f"{python} {FLOOD.as_posix()} {FLOOD_SECONDS} {MARKER.as_posix()}"
    line = command if raw_shell else f"!{command}"
    for attempt in range(3):
        _api("POST", "/pty/input", {"data": "\x1b"})
        time.sleep(0.5)
        _api("POST", "/pty/input", {"data": line})
        time.sleep(0.5)
        _api("POST", "/pty/input", {"data": "\r"})
        deadline = time.time() + 8
        while time.time() < deadline:
            if MARKER.exists():
                return True
            time.sleep(0.2)
        print(f"injection attempt {attempt + 1} did not start flood; retrying")
    return False


def gpu_sampler(
    pids: list[int], samples: list[tuple[float, float, float]], stop: threading.Event
) -> None:
    pattern = "|".join(f"pid_{pid}_" for pid in pids)
    script = f"""
$regex='{pattern}'
while($true){{
  $s=Get-Counter '\\GPU Engine(*)\\Utilization Percentage' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty CounterSamples | Where-Object {{ $_.InstanceName -match $regex }}
  $sum=($s | Measure-Object -Property CookedValue -Sum).Sum
  $max=($s | Measure-Object -Property CookedValue -Maximum).Maximum
  if($null -eq $sum){{ $sum=0 }}; if($null -eq $max){{ $max=0 }}
  [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString() + ' ' + $sum + ' ' + $max
  Start-Sleep -Milliseconds 400}}
"""
    process = subprocess.Popen(
        ["powershell", "-NoProfile", "-Command", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if stop.is_set():
                break
            parts = line.split()
            if len(parts) != 3:
                continue
            try:
                ticks, total, peak = int(parts[0]), float(parts[1]), float(parts[2])
            except ValueError:
                continue
            samples.append((ticks / 1000.0, total, peak))
    finally:
        process.kill()
        process.wait(timeout=10)


class Collector:
    def __init__(self, pids: list[int]) -> None:
        self.pids = pids
        self.cpu: list[tuple[float, float]] = []
        self.gpu: list[tuple[float, float, float]] = []
        self.stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def __enter__(self) -> Self:
        gpu = threading.Thread(
            target=gpu_sampler, args=(self.pids, self.gpu, self.stop), daemon=True
        )
        gpu.start()
        self._threads.append(gpu)
        cpu = threading.Thread(target=self._cpu_loop, daemon=True)
        cpu.start()
        self._threads.append(cpu)
        return self

    def _cpu_loop(self) -> None:
        cores = psutil.cpu_count(logical=True) or 1
        processes = [psutil.Process(pid) for pid in self.pids]
        previous = (
            time.monotonic(),
            sum(p.cpu_times().user + p.cpu_times().system for p in processes),
        )
        while not self.stop.wait(0.5):
            now = time.monotonic()
            current = sum(p.cpu_times().user + p.cpu_times().system for p in processes)
            elapsed = now - previous[0]
            if elapsed <= 0:
                continue
            percent = (current - previous[1]) / elapsed / cores * 100.0
            self.cpu.append((time.time(), percent))
            previous = (now, current)

    def __exit__(self, *exc: object) -> None:
        self.stop.set()
        for thread in self._threads:
            thread.join(timeout=5)


def stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"avg": 0.0, "peak": 0.0}
    return {
        "avg": round(sum(values) / len(values), 2),
        "peak": round(max(values), 2),
    }


def window(samples: list[tuple[float, float, float]], start: float, end: float) -> dict[str, float]:
    picked = [item for item in samples if start <= item[0] <= end]
    return {
        "avg_sum": round(sum(item[1] for item in picked) / max(1, len(picked)), 2),
        "peak_sum": round(max((item[1] for item in picked), default=0.0), 2),
        "avg_engine": round(sum(item[2] for item in picked) / max(1, len(picked)), 2),
        "peak_engine": round(max((item[2] for item in picked), default=0.0), 2),
        "samples": len(picked),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-inject", action="store_true")
    parser.add_argument("--raw-shell", action="store_true", help="PTY 是原生 shell，直接输入命令")
    args = parser.parse_args()

    pid = app_pid()
    pids = webview_pids(pid)
    if not pids:
        raise SystemExit("未找到 msedgewebview2 子进程")
    print(f"app pid={pid} webview pids={pids}")
    restore_window(pid)
    time.sleep(1)

    marker_was_present = MARKER.exists()
    if not args.no_inject:
        marker_was_present = False
        select_tab(pid, 0)
        time.sleep(2)
        with Collector(pids) as collector:
            baseline_start = time.time()
            time.sleep(BASELINE_SECONDS)
            baseline_end = time.time()

            select_tab(pid, 2)
            time.sleep(2)
            if not inject_flood(args.raw_shell):
                select_tab(pid, 0)
                raise SystemExit(
                    "洪流命令三次都未启动：请确认 TUI 在空闲提示符，或手动执行一次 "
                    f"`!{sys.executable.replace(chr(92), '/')} {FLOOD.as_posix()} 10` "
                    "看是否有权限/模式提示"
                )
            marker_was_present = True
            time.sleep(2)
            b_start = time.time()
            time.sleep(SCENARIO_SECONDS)
            b_end = time.time()

            select_tab(pid, 0)
            a_start = time.time()
            time.sleep(SCENARIO_SECONDS)
            a_end = time.time()
    else:
        with Collector(pids) as collector:
            baseline_start = time.time()
            time.sleep(BASELINE_SECONDS)
            baseline_end = time.time()
            b_start = b_end = a_start = a_end = baseline_end

    report = {
        "app_pid": pid,
        "webview_pids": pids,
        "cpu_cores": psutil.cpu_count(logical=True),
        "injection": {
            "attempted": not args.no_inject,
            "flood_marker_seen": marker_was_present,
        },
        "baseline": {
            "duration_s": BASELINE_SECONDS,
            **stats([v for t, v in collector.cpu if baseline_start <= t <= baseline_end]),
            "gpu": window(collector.gpu, baseline_start, baseline_end),
        },
        "scenario_b_terminal_visible": {
            "duration_s": SCENARIO_SECONDS,
            **stats([v for t, v in collector.cpu if b_start <= t <= b_end]),
            "gpu": window(collector.gpu, b_start, b_end),
        },
        "scenario_a_chat_hidden_terminal": {
            "duration_s": SCENARIO_SECONDS,
            **stats([v for t, v in collector.cpu if a_start <= t <= a_end]),
            "gpu": window(collector.gpu, a_start, a_end),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        try:
            MARKER.unlink(missing_ok=True)
        except OSError:
            pass
