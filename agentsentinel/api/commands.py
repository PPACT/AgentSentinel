"""Fast Path 固定命令的生产接线。

固定死命令直接调用角色的确定性 ``handle``，不注入模型、不经过 LLM（总体规划 §11.1）。
本模块不依赖 FastAPI，便于离线测试。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from agentsentinel.roles.tester import Tester
from agentsentinel.roles.writer import Writer
from agentsentinel.workspace import Workspace

CommandHandler = Callable[[str], None]

RUN_TEST_TASK = "fast-path run-test"
SYNC_DOCS_TASK = "fast-path sync-docs"


def default_command_handlers(workspace: Workspace) -> dict[str, CommandHandler]:
    """返回默认命令表：``run-test`` 交 Tester、``sync-docs`` 交 Writer。"""

    def run_test(arguments: str) -> None:
        Tester().handle(arguments or RUN_TEST_TASK, workspace)

    def sync_docs(arguments: str) -> None:
        Writer().handle(arguments or SYNC_DOCS_TASK, workspace)

    return {"run-test": run_test, "sync-docs": sync_docs}


def dispatch(
    handlers: Mapping[str, CommandHandler],
    command: str,
    arguments: str,
) -> None:
    """按命令名分发；分类器只会发两个固定命令，其余视为调用方错误。"""
    handler = handlers.get(command)
    if handler is None:
        raise ValueError(f"未知的 Fast Path 命令: {command}")
    handler(arguments)
