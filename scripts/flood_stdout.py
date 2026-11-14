"""向 stdout 持续写行，用于终端高压测试。

用法：python scripts/flood_stdout.py <秒数> [marker文件]
在原生 shell 工作区的终端里运行；输出会占满 PTY，模拟极端高频输出。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
marker = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).with_suffix(".started")
marker.parent.mkdir(parents=True, exist_ok=True)
marker.write_text(str(__import__("os").getpid()), encoding="utf-8")
line = ("0123456789abcdef" * 8 + "\n") * 200
end = time.time() + seconds
try:
    while time.time() < end:
        sys.stdout.write(line)
        sys.stdout.flush()
finally:
    try:
        marker.unlink()
    except OSError:
        pass
