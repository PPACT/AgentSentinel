"""P1-1 入口判定与签名校验的正反样本。"""

from __future__ import annotations

import hashlib
import hmac

from agentsentinel.api.ingress import (
    IngressDecision,
    IngressEvent,
    classify,
    verify_signature,
)

# secret-fixture: 分段拼接的测试值，避免完整密钥形态落盘
SIGNING_VALUE = "test" + "-secret"


def _event(text: str, labels: tuple[str, ...] = ()) -> IngressEvent:
    return IngressEvent(source="slack", text=text, labels=labels)


def _signature(body: bytes = b"payload", secret: str = SIGNING_VALUE) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_wakeword_is_slow_path_and_stripped() -> None:
    result = classify(_event("@AgentSentinel 重构鉴权"))

    assert result.decision is IngressDecision.SLOW_PATH
    assert result.task == "重构鉴权"


def test_wakeword_is_case_insensitive() -> None:
    result = classify(_event("@agentsentinel 重构鉴权"))

    assert result.decision is IngressDecision.SLOW_PATH
    assert result.task == "重构鉴权"


def test_wakeword_only_falls_back_to_original_text() -> None:
    result = classify(_event("@AgentSentinel"))

    assert result.decision is IngressDecision.SLOW_PATH
    assert result.task == "@AgentSentinel"


def test_bracket_trigger_is_slow_path() -> None:
    for text in ("[Bug] 登录失败", "[Feature] 导出"):
        result = classify(_event(text))
        assert result.decision is IngressDecision.SLOW_PATH, text
        assert result.task == text


def test_label_trigger_is_slow_path() -> None:
    for labels in (("bug",), ("[Feature]",), (" [Bug] ",)):
        result = classify(_event("普通描述", labels))
        assert result.decision is IngressDecision.SLOW_PATH, labels
        assert result.task == "普通描述"


def test_word_trigger_is_slow_path() -> None:
    result = classify(_event("修复 bug"))

    assert result.decision is IngressDecision.SLOW_PATH
    assert result.task == "修复 bug"


def test_debug_does_not_trigger_slow_path() -> None:
    result = classify(_event("debug 模式说明"))

    assert result.decision is IngressDecision.DROP


def test_silent_commands_are_fast_path() -> None:
    cases = [
        ("/run-test", "run-test", ""),
        ("/run-test smoke", "run-test", "smoke"),
        ("/sync-docs", "sync-docs", ""),
        ("  /sync-docs   all  ", "sync-docs", "all"),
    ]
    for text, command, arguments in cases:
        result = classify(_event(text))
        assert result.decision is IngressDecision.FAST_PATH, text
        assert result.command == command, text
        assert result.arguments == arguments, text


def test_silent_command_has_priority_over_trigger() -> None:
    result = classify(_event("/run-test @AgentSentinel bug"))

    assert result.decision is IngressDecision.FAST_PATH
    assert result.command == "run-test"
    assert result.arguments == "@AgentSentinel bug"


def test_similar_command_prefix_is_drop() -> None:
    for text in ("/run-testing", "/run-testx"):
        result = classify(_event(text))
        assert result.decision is IngressDecision.DROP, text


def test_plain_or_empty_text_is_drop() -> None:
    for text in ("普通聊天", "", "   "):
        result = classify(_event(text))
        assert result.decision is IngressDecision.DROP, text


def test_unrelated_label_is_drop() -> None:
    result = classify(_event("普通聊天", ("bugfix",)))

    assert result.decision is IngressDecision.DROP


def test_valid_signature_is_accepted() -> None:
    body = b'{"text": "x"}'

    assert verify_signature(secret=SIGNING_VALUE, body=body, header=_signature(body)) is True


def test_wrong_digest_is_rejected() -> None:
    body = b"payload"

    assert verify_signature(secret=SIGNING_VALUE, body=body, header=_signature(b"other")) is False


def test_missing_or_malformed_header_is_rejected() -> None:
    body = b"payload"
    headers = [None, "", "sha256=", "sha1=abc", "deadbeef", "sha256=not-hex"]

    for header in headers:
        assert verify_signature(secret=SIGNING_VALUE, body=body, header=header) is False, header


def test_empty_secret_is_rejected() -> None:
    body = b"payload"

    assert verify_signature(secret="", body=body, header=_signature(body, secret="")) is False


def test_non_ascii_digest_is_rejected_not_raised() -> None:
    body = b"payload"

    assert verify_signature(secret=SIGNING_VALUE, body=body, header="sha256=密钥") is False
