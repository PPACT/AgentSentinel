"""真实模型专家角色的工作区受限工具。

只暴露最小工具集：读、写在白名单目录内完成，路径经 ``Workspace`` 校验；
不把 AgentScope 内置的 raw ``Bash`` / ``Write`` / ``Edit`` 交给专家角色。
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, Toolkit

from agentsentinel.contracts import TestReport, TestStatus
from agentsentinel.workspace import Workspace

_TOOL_ALLOWED = PermissionDecision(
    behavior=PermissionBehavior.ALLOW,
    message="工作区受限工具，已按角色范围放行。",
)


def build_workspace_toolkit(tools: Mapping[str, Callable[..., Any]]) -> Toolkit:
    """把给定的工作区受限函数注册为 Toolkit；不注册任何内置 raw 工具。"""
    return Toolkit(
        tools=[
            FunctionTool(func, name=name, permission=_TOOL_ALLOWED) for name, func in tools.items()
        ]
    )


class WorkspaceToolbox:
    """按角色划定读写范围的工具实现，并记录实际产物路径。"""

    def __init__(
        self,
        workspace: Workspace,
        *,
        read_scope: str | None = None,
        write_scope: str | None = None,
        can_submit_report: bool = False,
    ) -> None:
        self._workspace = workspace
        self._read_scope = read_scope
        self._write_scope = write_scope
        self._can_submit_report = can_submit_report
        self.artifacts: list[Path] = []
        self.report_status: TestStatus | None = None
        self.report_summary = ""

    def file_read(self, path: str) -> str:
        """读取工作区内的 UTF-8 文本文件。

        Args:
            path: 相对工作区的路径；角色有读范围时只允许范围内的路径。

        Returns:
            文件全文。
        """
        return self._resolve(path, self._read_scope).read_text(encoding="utf-8")

    def file_write(self, path: str, content: str) -> str:
        """写入工作区内的 UTF-8 文本文件。

        Args:
            path: 相对工作区的路径；只允许角色写范围内的路径。
            content: 完整文本内容。

        Returns:
            实际写入路径。
        """
        target = self._resolve(path, self._write_scope)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._record(target)
        return str(target)

    def file_edit(self, path: str, old: str, new: str) -> str:
        """替换工作区内文本文件中的首个匹配片段。

        Args:
            path: 相对工作区的路径；只允许角色写范围内的路径。
            old: 待替换的原文。
            new: 替换后的文本。

        Returns:
            实际修改路径。
        """
        target = self._resolve(path, self._write_scope, must_exist=True)
        text = target.read_text(encoding="utf-8")
        if old not in text:
            raise ValueError(f"待替换文本不存在: {path}")
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        self._record(target)
        return str(target)

    def submit_test_report(self, status: str, summary: str, details: str = "") -> str:
        """提交结构化测试报告；未调用本工具视为未提交。

        Args:
            status: 测试结论，PASS 或 FAIL。
            summary: 一句话结论。
            details: 可选的失败细节。

        Returns:
            报告文件路径。
        """
        if not self._can_submit_report:
            raise ValueError("当前角色不允许提交测试报告")
        verdict = TestStatus(status.strip().upper())
        report = TestReport(
            status=verdict,
            summary=summary,
            details=details,
            timestamp=datetime.now(UTC),
        )
        target = self._workspace.write_report(report)
        self.report_status = verdict
        self.report_summary = summary
        self._record(target)
        return str(target)

    def _resolve(self, path: str, scope: str | None, *, must_exist: bool = False) -> Path:
        if Path(path).is_absolute():
            raise ValueError(f"只接受工作区相对路径: {path}")
        target = self._workspace._resolve(path)
        if scope is not None:
            base = (self._workspace.root / scope).resolve()
            if not target.resolve().is_relative_to(base):
                raise ValueError(f"路径越出 {scope}/ 目录: {path}")
        if must_exist and not target.is_file():
            raise FileNotFoundError(f"文件不存在: {path}")
        return target

    def _record(self, path: Path) -> None:
        if path not in self.artifacts:
            self.artifacts.append(path)
