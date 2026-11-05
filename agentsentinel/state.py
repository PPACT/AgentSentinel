"""状态流转日志：向 workspace/reports/pipeline-state.jsonl 追加 JSONL。

与 ``audit.py`` 同构但独立：状态流转不进 push-log.jsonl，审计字段不混入状态。
"""

from pathlib import Path

from agentsentinel.contracts import StateRecord


class StateLogger:
    """追加写状态流转记录；写入失败直接抛错，不吞异常。"""

    def __init__(self, log_path: Path | str) -> None:
        self._log_path = Path(log_path)

    @property
    def log_path(self) -> Path:
        return self._log_path

    def record(self, record: StateRecord) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{record.model_dump_json()}\n")
            stream.flush()
