"""脱敏扫描接口与 S1 桩实现。

S1 只固定接口；detect-secrets 与自定义正则规则在 M2 落地。
"""

from dataclasses import dataclass
from typing import Protocol

from agentsentinel.contracts import ScanVerdict


@dataclass(frozen=True)
class ScanResult:
    verdict: ScanVerdict
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict is ScanVerdict.ALLOW


class Sanitizer(Protocol):
    def scan(self, content: str) -> ScanResult: ...


class AllowAllSanitizer:
    """放行所有内容。TODO(M2): 替换为 detect-secrets + 自定义规则。"""

    def scan(self, content: str) -> ScanResult:
        return ScanResult(verdict=ScanVerdict.ALLOW)


class DenyAllSanitizer:
    """固定返回 DENY，仅用于离线验证阻断路径。TODO(M2): 由真实规则取代。"""

    def __init__(self, reason: str = "stub deny") -> None:
        self._reason = reason

    def scan(self, content: str) -> ScanResult:
        return ScanResult(verdict=ScanVerdict.DENY, reason=self._reason)
