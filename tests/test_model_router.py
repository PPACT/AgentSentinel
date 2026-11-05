"""P2-3 ModelRouter：角色→档位映射、同档缓存、构造参数与缺配置 fail-closed。"""

from typing import ClassVar

import pytest
from agentscope.model import OpenAIChatModel

from agentsentinel.config import Settings
from agentsentinel.models import ModelRouter
from agentsentinel.models import router as router_module

MODEL_ENV_VARS = (
    "SENTINEL_MODEL_BACKEND",
    "SENTINEL_PREMIUM_BASE_URL",
    "SENTINEL_PREMIUM_MODEL",
    "SENTINEL_PREMIUM_API_KEY",
    "SENTINEL_PREMIUM_TEMPERATURE",
    "SENTINEL_PREMIUM_MAX_TOKENS",
    "SENTINEL_ECONOMY_BASE_URL",
    "SENTINEL_ECONOMY_MODEL",
    "SENTINEL_ECONOMY_API_KEY",
    "SENTINEL_ECONOMY_TEMPERATURE",
    "SENTINEL_ECONOMY_MAX_TOKENS",
)


class _FakeModel:
    """替代 OpenAIChatModel，只记录构造参数，不建客户端、不触网。"""

    Parameters = OpenAIChatModel.Parameters
    instances: ClassVar[list["_FakeModel"]] = []

    def __init__(
        self,
        *,
        credential: object,
        model: str,
        parameters: OpenAIChatModel.Parameters,
        stream: bool,
    ) -> None:
        self.credential = credential
        self.model = model
        self.parameters = parameters
        self.stream = stream
        _FakeModel.instances.append(self)


@pytest.fixture(autouse=True)
def _clean_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in MODEL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[_FakeModel]:
    _FakeModel.instances = []
    monkeypatch.setattr(router_module, "OpenAIChatModel", _FakeModel)
    return _FakeModel.instances


def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("SENTINEL_PREMIUM_MODEL", "premium-id")
    monkeypatch.setenv("SENTINEL_PREMIUM_API_KEY", "premium-key")
    monkeypatch.setenv("SENTINEL_PREMIUM_TEMPERATURE", "0.3")
    monkeypatch.setenv("SENTINEL_PREMIUM_MAX_TOKENS", "1024")
    monkeypatch.setenv("SENTINEL_ECONOMY_MODEL", "economy-id")
    monkeypatch.setenv("SENTINEL_ECONOMY_API_KEY", "economy-key")
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_role_tiers_share_models_within_tier(
    monkeypatch: pytest.MonkeyPatch, built: list[_FakeModel]
) -> None:
    router = ModelRouter(_settings(monkeypatch))

    coder = router.for_role("coder")
    tester = router.for_role("Tester")
    orchestrator = router.for_role("orchestrator")
    writer = router.for_role("writer")
    manager = router.for_role("manager")

    assert coder is tester
    assert orchestrator is writer
    assert coder is not orchestrator
    assert manager is None
    assert len(built) == 2


def test_unknown_role_raises(built: list[_FakeModel]) -> None:
    router = ModelRouter(Settings(_env_file=None))  # type: ignore[call-arg]

    with pytest.raises(ValueError):
        router.for_role("Reviewer")

    assert built == []


def test_base_url_is_omitted_when_empty(
    monkeypatch: pytest.MonkeyPatch, built: list[_FakeModel]
) -> None:
    router = ModelRouter(_settings(monkeypatch))

    router.for_role("coder")

    credential = built[0].credential
    assert getattr(credential, "base_url", "missing") is None


def test_base_url_is_passed_when_set(
    monkeypatch: pytest.MonkeyPatch, built: list[_FakeModel]
) -> None:
    monkeypatch.setenv("SENTINEL_PREMIUM_BASE_URL", "http://localhost:11434/v1")
    router = ModelRouter(_settings(monkeypatch))

    router.for_role("coder")

    credential = built[0].credential
    assert getattr(credential, "base_url", None) == "http://localhost:11434/v1"


def test_construction_parameters(monkeypatch: pytest.MonkeyPatch, built: list[_FakeModel]) -> None:
    router = ModelRouter(_settings(monkeypatch))

    router.for_role("coder")

    model = built[0]
    assert model.model == "premium-id"
    assert model.parameters.temperature == 0.3
    assert model.parameters.max_tokens == 1024
    assert model.stream is False


def test_missing_config_fails_before_building(
    monkeypatch: pytest.MonkeyPatch, built: list[_FakeModel]
) -> None:
    monkeypatch.setenv("SENTINEL_ECONOMY_MODEL", "economy-id")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    with pytest.raises(RuntimeError) as exc_info:
        ModelRouter(settings).for_role("orchestrator")

    assert "SENTINEL_ECONOMY_API_KEY" in str(exc_info.value)
    assert built == []


@pytest.mark.parametrize("api_key", ["", "   "], ids=["empty", "whitespace"])
def test_blank_api_key_is_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch, built: list[_FakeModel], api_key: str
) -> None:
    """`.env.example` 的空值会解析成 SecretStr('')，必须按缺失处理。"""
    monkeypatch.setenv("SENTINEL_PREMIUM_MODEL", "premium-id")
    monkeypatch.setenv("SENTINEL_PREMIUM_API_KEY", api_key)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    with pytest.raises(RuntimeError) as exc_info:
        ModelRouter(settings).for_role("coder")

    assert "SENTINEL_PREMIUM_API_KEY" in str(exc_info.value)
    assert built == []


@pytest.mark.parametrize("model_id", ["", "   "], ids=["empty", "whitespace"])
def test_blank_model_is_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch, built: list[_FakeModel], model_id: str
) -> None:
    monkeypatch.setenv("SENTINEL_PREMIUM_MODEL", model_id)
    monkeypatch.setenv("SENTINEL_PREMIUM_API_KEY", "premium-key")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    with pytest.raises(RuntimeError) as exc_info:
        ModelRouter(settings).for_role("coder")

    assert "SENTINEL_PREMIUM_MODEL" in str(exc_info.value)
    assert built == []
