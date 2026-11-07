"""P1 dummy worker：写 pid / env_keys，收到 ``GO`` 后派生子进程并保持存活。

由 launcher 在把本进程加入 Job Object 之后发送 ``GO``，保证子进程继承 Job。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path


def _heartbeat_interval() -> float:
    raw = os.environ.get("SENTINEL_ORCH_HEARTBEAT_SECONDS", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    return value if value > 0 else 30.0


def _heartbeat_loop(role: str, task_id: str, url: str, token: str, interval: float) -> None:
    payload = json.dumps({"role": role, "task_id": task_id}).encode("utf-8")
    endpoint = url.rstrip("/") + "/heartbeat"
    while True:
        try:
            request = urllib.request.Request(
                endpoint,
                data=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=2):
                pass
        except Exception:  # noqa: BLE001, S110 - 心跳失败不终止 worker
            pass
        time.sleep(interval)


def _write(path: Path, payload: dict[str, object]) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="P1 dummy worker")
    parser.add_argument("--role", required=True)
    parser.add_argument("--pid-file", required=True)
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args()
    pid_file = Path(args.pid_file)

    if args.child:
        time.sleep(args.seconds)
        return 0

    payload: dict[str, object] = {
        "pid": os.getpid(),
        "role": args.role,
        "env_keys": sorted(os.environ),
    }
    _write(pid_file, payload)
    if not sys.stdin.readline().strip():
        return 1

    child = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--role",
            args.role,
            "--pid-file",
            str(pid_file) + ".child",
            "--seconds",
            str(args.seconds),
            "--child",
        ]
    )
    payload["child_pid"] = child.pid
    _write(pid_file, payload)
    url = os.environ.get("ORCHESTRATOR_URL", "").strip()
    token = os.environ.get("ORCHESTRATOR_TOKEN", "").strip()
    task_id = os.environ.get("TASK_ID", "").strip()
    if url and token and task_id:
        threading.Thread(
            target=_heartbeat_loop,
            args=(args.role, task_id, url, token, _heartbeat_interval()),
            daemon=True,
        ).start()
    time.sleep(args.seconds)
    child.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
