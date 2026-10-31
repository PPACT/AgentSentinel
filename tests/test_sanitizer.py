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
    # 不在仓库里保留完整密钥形态：分段拼接，避免被推送保护当成真实密钥。
    secret = "sk-" + "abcdefghijklmnopqrstuvwxyz123456"

    result = RegexSanitizer().scan(f'key = "{secret}"')

    assert result.verdict is ScanVerdict.DENY
    assert result.reason.startswith("custom:api_key")
    assert secret not in result.reason


def test_internal_ip_is_denied() -> None:
    result = RegexSanitizer().scan("host = '192.168.1.10'")

    assert result.verdict is ScanVerdict.DENY
    assert result.reason.startswith("custom:private_ip")


def test_password_pattern_is_denied() -> None:
    result = RegexSanitizer().scan('password = "hunter2"')

    assert result.verdict is ScanVerdict.DENY
    assert result.reason.startswith("custom:password")


def test_id_card_number_is_denied() -> None:
    # 999999 不是有效行政区划码，且校验位无效：只验证格式匹配，不对应任何真实身份。
    result = RegexSanitizer().scan("身份证：999999199001011234")

    assert result.verdict is ScanVerdict.DENY
    assert result.reason.startswith("custom:id_card")


def test_detect_secrets_engine_catches_non_custom_secret() -> None:
    # Stripe 风格密钥不在自定义正则内，只能由 detect-secrets 插件命中。
    stripe_sample = "sk_" + "live_" + "51H" + "abcdefghijklmnopqrstuv"
    result = RegexSanitizer().scan(f'stripe_key = "{stripe_sample}"')

    assert result.verdict is ScanVerdict.DENY
    assert result.reason.startswith("detect-secrets:")


def test_sanitizer_never_returns_not_run() -> None:
    sanitizer = RegexSanitizer()

    assert sanitizer.scan(CLEAN_CODE).verdict in {ScanVerdict.ALLOW, ScanVerdict.DENY}
    assert sanitizer.scan('password = "hunter2"').verdict in {
        ScanVerdict.ALLOW,
        ScanVerdict.DENY,
    }


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
