"""脱敏扫描：detect-secrets 引擎 + 自定义正则（规范 §3.1）。

DENY 为业务终态：扫描器不保存状态、不重试，同样输入始终得到同样结论。
"""

import re
from dataclasses import dataclass
from typing import Protocol

from detect_secrets.core.scan import scan_line
from detect_secrets.settings import transient_settings

from agentsentinel.contracts import ScanVerdict

# 只启用高置信度插件。熵类插件在 adhoc 扫描下会把 main/str 判为 Base64，
# 误报不可接受；随机密钥由自定义正则兜底。
DETECT_SECRETS_PLUGINS: tuple[dict[str, object], ...] = (
    {"name": "AWSKeyDetector"},
    {"name": "GitHubTokenDetector"},
    {"name": "GitLabTokenDetector"},
    {"name": "OpenAIDetector"},
    {"name": "PrivateKeyDetector"},
    {"name": "SlackDetector"},
    {"name": "StripeDetector"},
    {"name": "TelegramBotTokenDetector"},
    {"name": "KeywordDetector"},
)

# 命中原因只写规则名与行号，绝不回显密钥本身。
CUSTOM_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "api_key",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}"
            r"|xox[baprs]-[A-Za-z0-9-]{10,})\b"
        ),
    ),
    (
        "private_ip",
        re.compile(
            r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
            r"|192\.168\.\d{1,3}\.\d{1,3}"
            r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
        ),
    ),
    (
        "password",
        re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"]{4,}"),
    ),
    (
        "id_card",
        re.compile(
            r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
            r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
        ),
    ),
)


@dataclass(frozen=True)
class ScanResult:
    verdict: ScanVerdict
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict is ScanVerdict.ALLOW


class Sanitizer(Protocol):
    def scan(self, content: str) -> ScanResult: ...


class RegexSanitizer:
    """规范 §3.1 的实现：自定义正则优先，其次 detect-secrets 插件。"""

    def __init__(
        self,
        plugins: tuple[dict[str, object], ...] = DETECT_SECRETS_PLUGINS,
    ) -> None:
        self._plugins = plugins

    def scan(self, content: str) -> ScanResult:
        for line_number, line in enumerate(content.splitlines(), start=1):
            for rule_name, pattern in CUSTOM_RULES:
                if pattern.search(line):
                    return ScanResult(
                        verdict=ScanVerdict.DENY,
                        reason=f"custom:{rule_name} (line {line_number})",
                    )

        hit = self._detect_secrets_hit(content)
        if hit is not None:
            line_number, secret_type = hit
            return ScanResult(
                verdict=ScanVerdict.DENY,
                reason=f"detect-secrets:{secret_type} (line {line_number})",
            )
        return ScanResult(verdict=ScanVerdict.ALLOW)

    def _detect_secrets_hit(self, content: str) -> tuple[int, str] | None:
        with transient_settings({"plugins_used": list(self._plugins)}):
            for line_number, line in enumerate(content.splitlines(), start=1):
                for secret in scan_line(line):
                    return (line_number, secret.type)
        return None


class AllowAllSanitizer:
    """放行所有内容，仅用于未接入治理的单元测试。"""

    def scan(self, content: str) -> ScanResult:
        return ScanResult(verdict=ScanVerdict.ALLOW)


class DenyAllSanitizer:
    """固定返回 DENY，仅用于验证阻断路径。"""

    def __init__(self, reason: str = "stub deny") -> None:
        self._reason = reason

    def scan(self, content: str) -> ScanResult:
        return ScanResult(verdict=ScanVerdict.DENY, reason=self._reason)
