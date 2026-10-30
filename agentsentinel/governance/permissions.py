"""权限判定接口与 S1 桩实现。

S1 只固定接口；权限矩阵与 AgentScope 权限模式映射在 M2 落地。
"""

from enum import StrEnum
from typing import Protocol


class PermissionDecision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    ASK = "ASK"


class PermissionPolicy(Protocol):
    def check(self, tool: str, caller: str) -> PermissionDecision: ...


class AllowAllPolicy:
    """放行所有调用。TODO(M2): 替换为规范 §3.2 的权限矩阵与调用者鉴权。"""

    def check(self, tool: str, caller: str) -> PermissionDecision:
        return PermissionDecision.ALLOW
