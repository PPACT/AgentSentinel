"""审计日志：向 workspace/audit/push-log.jsonl 追加 JSONL。

路径与字段依据：架构规范 §3.3。
"""

import logging
from pathlib import Path
from typing import cast

import structlog

from agentsentinel.contracts import AuditRecord

DEFAULT_LOG_PATH = Path("workspace/audit/push-log.jsonl")


class AuditLogger:
    """追加写审计记录；写入失败直接抛错，不吞异常。"""

    def __init__(self, log_path: Path | str = DEFAULT_LOG_PATH) -> None:
        self._log_path = Path(log_path)
        self._renderer = structlog.processors.JSONRenderer(ensure_ascii=False)

    @property
    def log_path(self) -> Path:
        return self._log_path

    def record(self, record: AuditRecord) -> None:
        line = cast(
            str,
            self._renderer(
                logging.getLogger("agentsentinel.audit"),
                "info",
                record.model_dump(mode="json"),
            ),
        )
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{line}\n")
            stream.flush()
