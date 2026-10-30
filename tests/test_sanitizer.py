"""脱敏扫描：自定义规则、detect-secrets 与 DENY 终态。"""

from agentsentinel.contracts import ScanVerdict
from agentsentinel.governance.sanitizer import (
    AllowAllSanitizer,
    DenyAllSanitizer,
    RegexSanitizer,
)

CLEAN_CODE = 'def main() -> str:\n    return "ok"\n'


def test_clean_code_is_allowed() -> None:
    result = RegexSanitizer().scan(CLEAN_CODE)

    assert result.verdict is ScanVerdict.ALLOW
    assert result.allowed
    assert result.reason == ""


def test_api_key_is_denied_without_echoing_it() -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"

    result = RegexSanitizer().scan(f'key = "{secret}"')

    assert result.verdict is ScanVerdict.DENY
    assert "API key" in result.reason
    assert secret not in result.reason


def test_internal_ip_is_denied() -> None:
    result = RegexSanitizer().scan("host = '192.168.1.10'")

    assert result.verdict is ScanVerdict.DENY
    assert "内网 IP" in result.reason


def test_password_pattern_is_denied() -> None:
    result = RegexSanitizer().scan('password = "hunter2"')

    assert result.verdict is ScanVerdict.DENY
    assert "密码" in result.reason


def test_id_card_number_is_denied() -> None:
    result = RegexSanitizer().scan("身份证：11010519491231002X")

    assert result.verdict is ScanVerdict.DENY
    assert "身份证号" in result.reason


def test_detect_secrets_engine_catches_non_custom_secret() -> None:
    # Stripe 风格密钥不在自定义正则内，只能由 detect-secrets 插件命中。
    stripe_sample = "sk_" + "live_" + "51H" + "abcdefghijklmnopqrstuv"
    result = RegexSanitizer().scan(f'stripe_key = "{stripe_sample}"')

    assert result.verdict is ScanVerdict.DENY
    assert "detect-secrets" in result.reason


def test_deny_is_terminal_and_repeatable() -> None:
    sanitizer = RegexSanitizer()
    content = 'password = "hunter2"'

    first = sanitizer.scan(content)
    second = sanitizer.scan(content)

    assert first.verdict is ScanVerdict.DENY
    assert second.verdict is ScanVerdict.DENY
    assert first == second


def test_stub_sanitizers_keep_s1_contract() -> None:
    assert AllowAllSanitizer().scan("anything").allowed
    denied = DenyAllSanitizer(reason="测试拦截").scan("anything")
    assert denied.verdict is ScanVerdict.DENY
    assert denied.reason == "测试拦截"
