"""Checkpoint / ContextSummary 的磁盘存储。

路径由 ``Workspace`` 统一给出：``reports/checkpoints/<role>.json`` 与
``reports/context-summary/<role>.json``。写入沿用 ``audit.py`` 的 UTF-8
风格，并额外使用同目录临时文件 + ``os.replace`` 原子替换，避免读到半份 JSON。
"""

import os
import tempfile
from pathlib import Path

from agentsentinel.contracts import Checkpoint, ContextSummary
from agentsentinel.workspace import Workspace


def _atomic_write_json(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


class CheckpointStore:
    """按角色保存最新一份 Checkpoint；读取失败返回 ``None``，不猜。"""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    def path_for(self, role: str) -> Path:
        return self._workspace.checkpoint_path(role)

    def save(self, checkpoint: Checkpoint) -> Path:
        path = self.path_for(checkpoint.role)
        _atomic_write_json(path, checkpoint.model_dump_json(indent=2))
        return path

    def load(self, role: str) -> Checkpoint | None:
        path = self.path_for(role)
        try:
            return Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 缺失或损坏都交给调用方决定降级，不在这里新建空 checkpoint。
            return None


class ContextSummaryStore:
    """按角色保存上下文摘要，角色之间互不覆盖。"""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    def path_for(self, role: str) -> Path:
        return self._workspace.context_summary_path(role)

    def save(self, summary: ContextSummary) -> Path:
        path = self.path_for(summary.role)
        _atomic_write_json(path, summary.model_dump_json(indent=2))
        return path

    def load(self, role: str) -> ContextSummary | None:
        path = self.path_for(role)
        try:
            return ContextSummary.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
