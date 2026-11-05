"""P2-3 模型配置：两档模板、扁平/嵌套覆盖、缺配置报错与密钥保密。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentsentinel.config import Settings
from agentsentinel.models import ModelRouter

MODEL_ENV_VARS = (
    "SENTINEL_MODEL_BACKEND",
    "SENTINEL_PREMIUM_BASE_URL",
    "SENTINEL_PREMIUM_MODEL",
    "SENTINEL_PREMIUM_API_KEY",
    "SENTINEL_PREMIUM_TEMPERATURE",
    "SENTINEL_PREMIUM_MAX_TOKENS",
    "SENTINEL_PREMIUM_MODEL__BASE_URL",
    "SENTINEL_PREMIUM_MODEL__MODEL",
    "SENTINEL_PREMIUM_MODEL__API_KEY",
    "SENTINEL_PREMIUM_MODEL__TEMPERATURE",
    "SENTINEL_PREMIUM_MODEL__MAX_TOKENS",
    "SENTINEL_ECONOMY_BASE_URL",
    "SENTINEL_ECONOMY_MODEL",
    "SENTINEL_ECONOMY_API_KEY",
    "SENTINEL_ECONOMY_TEMPERATURE",
    "SENTINEL_ECONOMY_MAX_TOKENS",
    "SENTINEL_ECONOMY_MODEL__BASE_URL",
    "SENTINEL_ECONOMY_MODEL__MODEL",
    "SENTINEL_ECONOMY_MODEL__API_KEY",
    "SENTINEL_ECONOMY_MODEL__TEMPERATURE",
    "SENTINEL_ECONOMY_MODEL__MAX_TOKENS",
)


@pytest.fixture(autouse=True)
def _clean_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离宿主机上可能存在的模型相关环境变量。"""
    for name in MODEL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_default_backend_is_stub_and_profiles_are_empty() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.model_backend == "stub"
    assert settings.premium_model.model == ""
    assert settings.premium_model.api_key is None
    assert settings.premium_model.temperature == 0.0
    assert settings.premium_model.max_tokens == 8192
    assert settings.economy_model.model == ""
    assert settings.economy_model.temperature == 0.2
    assert settings.economy_model.max_tokens == 4096


def test_flat_env_overrides_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_PREMIUM_BASE_URL", "https://premium.example/v1")
    monkeypatch.setenv("SENTINEL_PREMIUM_MODEL", "premium-id")
    monkeypatch.setenv("SENTINEL_PREMIUM_API_KEY", "premium-key")
    monkeypatch.setenv("SENTINEL_PREMIUM_TEMPERATURE", "0.3")
    monkeypatch.setenv("SENTINEL_PREMIUM_MAX_TOKENS", "1024")
    monkeypatch.setenv("SENTINEL_ECONOMY_MODEL", "economy-id")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.premium_model.base_url == "https://premium.example/v1"
    assert settings.premium_model.model == "premium-id"
    assert settings.premium_model.api_key is not None
    assert settings.premium_model.api_key.get_secret_value() == "premium-key"
    assert settings.premium_model.temperature == 0.3
    assert settings.premium_model.max_tokens == 1024
    assert settings.economy_model.model == "economy-id"


def test_nested_env_overrides_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_PREMIUM_MODEL__BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("SENTINEL_PREMIUM_MODEL__MODEL", "local-premium")
    monkeypatch.setenv("SENTINEL_PREMIUM_MODEL__API_KEY", "local")
    monkeypatch.setenv("SENTINEL_ECONOMY_MODEL__MODEL", "local-economy")
    monkeypatch.setenv("SENTINEL_ECONOMY_MODEL__TEMPERATURE", "0.7")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.premium_model.base_url == "http://localhost:11434/v1"
    assert settings.premium_model.model == "local-premium"
    assert settings.premium_model.api_key is not None
    assert settings.premium_model.api_key.get_secret_value() == "local"
    assert settings.economy_model.model == "local-economy"
    assert settings.economy_model.temperature == 0.7


def test_real_mode_missing_config_names_missing_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENTINEL_MODEL_BACKEND", "real")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    with pytest.raises(RuntimeError) as exc_info:
        ModelRouter(settings).for_role("coder")

    message = str(exc_info.value)
    assert "SENTINEL_PREMIUM_MODEL" in message
    assert "SENTINEL_PREMIUM_API_KEY" in message
    assert "stub" in message


def test_unknown_backend_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_MODEL_BACKEND", "vendor-specific")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_api_keys_never_appear_in_repr_or_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_PREMIUM_API_KEY", "premium-super-secret")
    monkeypatch.setenv("SENTINEL_ECONOMY_API_KEY", "economy-super-secret")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    rendered = (
        f"{settings!r} {settings!s} {settings.model_dump()} "
        f"{settings.model_dump_json()} {settings.premium_model!r} {settings.economy_model!r}"
    )

    assert "premium-super-secret" not in rendered
    assert "economy-super-secret" not in rendered


def test_env_example_only_templates_two_tiers() -> None:
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")

    assert "SENTINEL_OPENAI_API_KEY" not in text
    assert "SENTINEL_PREMIUM_MODEL=" in text
    assert "SENTINEL_ECONOMY_MODEL=" in text
    assert "SENTINEL_MODEL_BACKEND=stub" in text
