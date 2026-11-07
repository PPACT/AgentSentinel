"""P0-2 的 dummy worker：打印 pid，等父进程放行后派生孙进程并保持存活。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

SLEEP_SECONDS = 120


def main() -> int:
    if "--child" in sys.argv:
        print(f"CHILD_PID={os.getpid()}", flush=True)
        time.sleep(SLEEP_SECONDS)
        return 0
    print(f"WORKER_PID={os.getpid()}", flush=True)
    if not sys.stdin.readline():
        return 1
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child"])
    print(f"CHILD_PID={child.pid}", flush=True)
    time.sleep(SLEEP_SECONDS)
    child.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
