"""共享 Workspace：产物目录、文本读写与测试报告路径。

目录与报告路径依据：架构规范 §2.2、§2.3、§2.4、§3.3。
"""

import re
from datetime import datetime
from pathlib import Path

from agentsentinel.contracts import TestReport

DEFAULT_ROOT = Path("workspace")
ARTIFACT_DIRS = ("code", "docs", "reports", "audit")
# 文件名里的时间戳需兼容 Windows，不能用冒号。
REPORT_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
CHECKPOINT_DIRNAME = "checkpoints"
CONTEXT_SUMMARY_DIRNAME = "context-summary"
_ROLE_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _role_slug(role: str) -> str:
    """角色名转文件名：大小写归一，拒绝空值与路径分隔符。"""
    slug = role.strip().casefold()
    if not _ROLE_SLUG.fullmatch(slug):
        raise ValueError(f"非法角色名: {role!r}")
    return slug


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

    @property
    def pipeline_state_path(self) -> Path:
        """会话状态流转日志；与测试报告同目录，独立于审计日志。"""
        return self.reports_dir / "pipeline-state.jsonl"

    @property
    def usage_path(self) -> Path:
        """模型用量与成本日志；与审计、状态流相互独立。"""
        return self.reports_dir / "usage.jsonl"

    @property
    def checkpoint_dir(self) -> Path:
        """每个角色仅保留最新一份恢复点。"""
        return self.reports_dir / CHECKPOINT_DIRNAME

    @property
    def context_summary_dir(self) -> Path:
        """按角色分文件存放上下文摘要，避免并发覆盖。"""
        return self.reports_dir / CONTEXT_SUMMARY_DIRNAME

    def checkpoint_path(self, role: str) -> Path:
        return self.checkpoint_dir / f"{_role_slug(role)}.json"

    def context_summary_path(self, role: str) -> Path:
        return self.context_summary_dir / f"{_role_slug(role)}.json"

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
