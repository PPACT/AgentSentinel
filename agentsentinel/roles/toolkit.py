"""角色工具白名单到 AgentScope Toolkit 的实际绑定。

提示词与白名单分离：``RoleSpec.tools`` 决定注册哪些工具，
``forbidden_tools`` 不得出现在白名单里；未在注册表中的工具直接报错，
避免「只声明、未绑定」。
"""

from collections.abc import Callable, Mapping
from typing import Any

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import (
    Bash,
    Edit,
    FunctionTool,
    Glob,
    Grep,
    Read,
    ToolBase,
    Toolkit,
    Write,
)

from agentsentinel.roles.base import RoleSpec

_TOOL_ALLOWED = PermissionDecision(
    behavior=PermissionBehavior.ALLOW,
    message="项目白名单工具，已按角色配置放行。",
)

# 项目工具名 → AgentScope 内置工具。内置工具名（Read/Write/...）注册时改为项目口径。
BUILTIN_TOOL_FACTORIES: dict[str, Callable[[], ToolBase]] = {
    "file_read": Read,
    "file_write": Write,
    "file_edit": Edit,
    "bash": Bash,
    "grep": Grep,
    "glob": Glob,
}


class UnknownToolError(ValueError):
    """白名单里出现没有绑定的工具名。"""


def build_toolkit(
    spec: RoleSpec,
    *,
    extra_tools: Mapping[str, Callable[..., Any]] | None = None,
) -> Toolkit:
    """按白名单构造 Toolkit；只注册 ``spec.tools`` 里的工具。

    ``extra_tools`` 提供项目自定义工具（编排分派、Git、扫描、审计等），
    但只有出现在白名单中的才会注册。
    """
    overlap = set(spec.tools) & set(spec.forbidden_tools)
    if overlap:
        raise ValueError(f"{spec.name} 的工具白名单包含禁止工具: {sorted(overlap)}")

    available = dict(extra_tools or {})
    tools: list[ToolBase] = []
    missing: list[str] = []
    for name in spec.tools:
        factory = BUILTIN_TOOL_FACTORIES.get(name)
        if factory is not None:
            tool = factory()
            tool.name = name
            tools.append(tool)
        elif name in available:
            tools.append(
                FunctionTool(available[name], name=name, permission=_TOOL_ALLOWED),
            )
        else:
            missing.append(name)

    if missing:
        raise UnknownToolError(f"{spec.name} 的工具未绑定实现: {missing}")

    return Toolkit(tools=tools)
