"""上下文卸载：把大体积内容落盘，只向主编排返回可寻址引用（规范 §2.1、§5）。"""

import base64
import json
import mimetypes
import re
from typing import Any

from agentscope.message import Base64Source, DataBlock, Msg, ToolResultBlock, URLSource
from pydantic import AnyUrl

from agentsentinel.workspace import Workspace

OFFLOAD_DIR = "reports/offload"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(value: str) -> str:
    cleaned = _UNSAFE.sub("-", value).strip(".-")
    return cleaned or "unnamed"


class WorkspaceOffloader:
    """实现 AgentScope ``Offloader`` 协议；卸载文件写入 workspace/reports/offload/。

    大体积产物留在磁盘，模型只看到路径引用，避免上下文爆炸。
    """

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    async def offload_context(self, session_id: str, msgs: list[Msg]) -> str:
        relative = f"{OFFLOAD_DIR}/context-{_safe_name(session_id)}.json"
        payload = [msg.model_dump(mode="json") for msg in msgs]
        return self._write_json(relative, payload)

    async def offload_tool_result(self, session_id: str, tool_result: ToolResultBlock) -> str:
        relative = f"{OFFLOAD_DIR}/tool-{_safe_name(tool_result.id)}.json"
        payload: dict[str, Any] = {
            "session_id": session_id,
            "tool": tool_result.model_dump(mode="json"),
        }
        return self._write_json(relative, payload)

    async def offload_data_block(self, block: DataBlock) -> DataBlock:
        if isinstance(block.source, URLSource):
            return block
        source = block.source
        if not isinstance(source, Base64Source):
            raise TypeError(f"不支持的 DataBlock 来源: {type(source).__name__}")
        suffix = mimetypes.guess_extension(source.media_type) or ".bin"
        relative = f"{OFFLOAD_DIR}/data-{_safe_name(block.id)}{suffix}"
        self._workspace.write_bytes(relative, base64.b64decode(source.data))
        return block.model_copy(
            update={
                "source": URLSource(
                    url=AnyUrl(f"workspace://{relative}"),
                    media_type=source.media_type,
                ),
            },
        )

    def _write_json(self, relative: str, payload: Any) -> str:
        self._workspace.write_text(relative, json.dumps(payload, ensure_ascii=False, indent=2))
        return f"workspace://{relative}"
