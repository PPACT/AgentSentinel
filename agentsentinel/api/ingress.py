"""入口治理：Fast Path / Slow Path 判定与 Webhook 签名校验。

规则依据：总体规划 §11.1 与交流区 P1-1 工单。本模块不依赖 FastAPI，
判定与验签均为纯函数，便于离线测试。
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from enum import StrEnum

WAKEWORD = "@AgentSentinel"
SIGNATURE_PREFIX = "sha256="

_FAST_COMMANDS = frozenset({"run-test", "sync-docs"})
_TRIGGER_LABELS = frozenset({"bug", "feature"})
_WAKEWORD_PATTERN = re.compile(re.escape(WAKEWORD), re.IGNORECASE)
_BRACKET_TRIGGER = re.compile(r"\[(bug|feature)\]", re.IGNORECASE)
_WORD_TRIGGER = re.compile(r"\b(bug|feature)\b", re.IGNORECASE)


class IngressDecision(StrEnum):
    """入口判定结果：丢弃、快速命令或进入完整流水线。"""

    DROP = "DROP"
    FAST_PATH = "FAST_PATH"
    SLOW_PATH = "SLOW_PATH"


@dataclass(frozen=True)
class IngressEvent:
    """平台无关的入口事件。"""

    source: str
    text: str
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class IngressResult:
    """判定结果；``command`` 仅 Fast Path 使用，``task`` 仅 Slow Path 使用。"""

    decision: IngressDecision
    command: str | None = None
    arguments: str = ""
    task: str = ""


def _split_command(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return "", ""
    token = parts[0]
    arguments = parts[1].strip() if len(parts) > 1 else ""
    return token, arguments


def _label_triggers(labels: tuple[str, ...]) -> bool:
    for label in labels:
        normalized = label.strip().strip("[]").strip().lower()
        if normalized in _TRIGGER_LABELS:
            return True
    return False


def _slow_path_task(text: str) -> str:
    stripped = text.strip()
    without_wakeword = _WAKEWORD_PATTERN.sub("", stripped).strip()
    return without_wakeword or stripped


def classify(event: IngressEvent) -> IngressResult:
    """按「静默命令 > 显式触发 > DROP」的优先级判定入口。"""
    token, arguments = _split_command(event.text)
    if token.startswith("/") and token[1:] in _FAST_COMMANDS:
        return IngressResult(
            decision=IngressDecision.FAST_PATH,
            command=token[1:],
            arguments=arguments,
        )

    text = event.text
    triggered = (
        _WAKEWORD_PATTERN.search(text) is not None
        or _label_triggers(event.labels)
        or _BRACKET_TRIGGER.search(text) is not None
        or _WORD_TRIGGER.search(text) is not None
    )
    if triggered:
        return IngressResult(
            decision=IngressDecision.SLOW_PATH,
            task=_slow_path_task(text),
        )

    return IngressResult(decision=IngressDecision.DROP)


def verify_signature(*, secret: str, body: bytes, header: str | None) -> bool:
    """校验 ``sha256=<hex>`` 形式的 HMAC 签名；任一环节不合法均返回 False。"""
    if not secret or not header:
        return False
    value = header.strip()
    if not value.startswith(SIGNATURE_PREFIX):
        return False
    digest = value[len(SIGNATURE_PREFIX) :].strip()
    if not digest:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    # 用 bytes 比较，避免 header 含非 ASCII 字符时 compare_digest 抛 TypeError。
    return hmac.compare_digest(expected.encode("ascii"), digest.lower().encode("utf-8"))
