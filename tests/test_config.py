"""Settings 的加载、覆盖与密钥保密测试。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentsentinel.config import Settings

_FIELD_ENV_NAMES = (
    "OPENAI_API_KEY",
    "GIT_USER_NAME",
    "GIT_USER_EMAIL",
    "GIT_REMOTE_URL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离宿主机上可能存在的同名环境变量（新旧前缀都不留）。"""
    for name in _FIELD_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"SENTINEL_{name}", raising=False)


def test_defaults_when_nothing_set() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.openai_api_key is None
    assert settings.git_user_name == ""
    assert settings.git_user_email is None
    assert settings.git_remote_url == ""


def test_prefixed_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("SENTINEL_GIT_USER_NAME", "bot")
    monkeypatch.setenv("SENTINEL_GIT_USER_EMAIL", "bot@example.com")
    monkeypatch.setenv("SENTINEL_GIT_REMOTE_URL", "https://example.com/repo.git")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-test-key"
    assert settings.git_user_name == "bot"
    assert settings.git_user_email == "bot@example.com"
    assert settings.git_remote_url == "https://example.com/repo.git"


def test_unprefixed_environment_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧的无前缀变量名不再生效，防止宿主环境同名变量误注入。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-legacy-key")
    monkeypatch.setenv("GIT_USER_NAME", "legacy-bot")
    monkeypatch.setenv("GIT_USER_EMAIL", "legacy@example.com")
    monkeypatch.setenv("GIT_REMOTE_URL", "https://legacy.example.com/repo.git")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.openai_api_key is None
    assert settings.git_user_name == ""
    assert settings.git_user_email is None
    assert settings.git_remote_url == ""


def test_invalid_email_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_GIT_USER_EMAIL", "not-an-email")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_dotenv_file_is_loaded(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SENTINEL_GIT_USER_NAME=from-dotenv\nSENTINEL_GIT_REMOTE_URL=https://example.com/x.git\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert settings.git_user_name == "from-dotenv"
    assert settings.git_remote_url == "https://example.com/x.git"


def test_api_key_never_appears_in_repr_or_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_OPENAI_API_KEY", "sk-super-secret")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    rendered = f"{settings!r} {settings!s} {settings.model_dump()} {settings.model_dump_json()}"

    assert "sk-super-secret" not in rendered
