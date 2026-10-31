"""共享 Workspace：产物目录、文本读写与测试报告路径。

目录与报告路径依据：架构规范 §2.2、§2.3、§2.4、§3.3。
"""

from datetime import datetime
from pathlib import Path

from agentsentinel.contracts import TestReport

DEFAULT_ROOT = Path("workspace")
ARTIFACT_DIRS = ("code", "docs", "reports", "audit")
# 文件名里的时间戳需兼容 Windows，不能用冒号。
REPORT_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


class Workspace:
    """以可注入根目录的共享工作区；默认根为 ./workspace。"""

    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def code_dir(self) -> Path:
        return self._root / "code"

    @property
    def docs_dir(self) -> Path:
        return self._root / "docs"

    @property
    def reports_dir(self) -> Path:
        return self._root / "reports"

    @property
    def audit_dir(self) -> Path:
        return self._root / "audit"

    def ensure(self) -> None:
        """创建四类产物目录；重复调用不破坏已有内容。"""
        for name in ARTIFACT_DIRS:
            (self._root / name).mkdir(parents=True, exist_ok=True)

    def write_text(self, relative_path: Path | str, content: str) -> Path:
        target = self._resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def write_bytes(self, relative_path: Path | str, content: bytes) -> Path:
        target = self._resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def read_text(self, relative_path: Path | str) -> str:
        return self._resolve(relative_path).read_text(encoding="utf-8")

    def report_path(self, timestamp: datetime) -> Path:
        name = f"test-{timestamp.strftime(REPORT_TIMESTAMP_FORMAT)}.json"
        return self.reports_dir / name

    def write_report(self, report: TestReport) -> Path:
        target = self.report_path(report.timestamp)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return target

    def _resolve(self, relative_path: Path | str) -> Path:
        candidate = Path(relative_path)
        target = candidate if candidate.is_absolute() else self._root / candidate
        # 防止产物写出工作区，越界即拒绝。
        if not target.resolve().is_relative_to(self._root.resolve()):
            raise ValueError(f"路径越出工作区: {relative_path}")
        return target
