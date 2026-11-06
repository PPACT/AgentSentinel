"""运行时配置：从环境变量与 .env 加载，密钥不回显。

环境变量统一使用 ``SENTINEL_`` 前缀，避免与宿主环境中的同名变量冲突。
P2-3 只提供两档模型配置（优异 / 平价），供应商由 ``base_url`` + ``model``
决定，不在代码里写死；``model_backend=stub`` 时不读取任何模型配置。

环境变量名既支持扁平写法（``SENTINEL_PREMIUM_MODEL`` 等），
也兼容嵌套写法（``SENTINEL_PREMIUM_MODEL__MODEL`` 等）。
"""

from pathlib import Path
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelProfile(BaseModel):
    """一档模型配置；供应商由 base_url + model 决定，不写死。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str | None = None
    model: str = ""
    api_key: SecretStr | None = None
    temperature: float = 0.0
    max_tokens: int | None = None


class Settings(BaseSettings):
    """项目配置。环境变量优先于 .env，.env 优先于代码内缺省值。"""

    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_",
        env_file=Path(".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # legacy：旧版单 Key 配置仅保留兼容，不参与模型路由；模型只读下面两档。
    openai_api_key: SecretStr | None = None
    webhook_secret: SecretStr | None = None
    git_user_name: str = ""
    git_user_email: EmailStr | None = None
    git_remote_url: str = ""

    # P2-3 两档模型；默认 stub，不读取模型配置、零网络。
    model_backend: Literal["stub", "real"] = "stub"

    premium_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SENTINEL_PREMIUM_BASE_URL", "SENTINEL_PREMIUM_MODEL__BASE_URL"
        ),
    )
    premium_model_id: str = Field(
        default="",
        validation_alias=AliasChoices("SENTINEL_PREMIUM_MODEL", "SENTINEL_PREMIUM_MODEL__MODEL"),
    )
    premium_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SENTINEL_PREMIUM_API_KEY", "SENTINEL_PREMIUM_MODEL__API_KEY"
        ),
    )
    premium_temperature: float = Field(
        default=0.0,
        validation_alias=AliasChoices(
            "SENTINEL_PREMIUM_TEMPERATURE", "SENTINEL_PREMIUM_MODEL__TEMPERATURE"
        ),
    )
    premium_max_tokens: int | None = Field(
        default=8192,
        validation_alias=AliasChoices(
            "SENTINEL_PREMIUM_MAX_TOKENS", "SENTINEL_PREMIUM_MODEL__MAX_TOKENS"
        ),
    )

    economy_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SENTINEL_ECONOMY_BASE_URL", "SENTINEL_ECONOMY_MODEL__BASE_URL"
        ),
    )
    economy_model_id: str = Field(
        default="",
        validation_alias=AliasChoices("SENTINEL_ECONOMY_MODEL", "SENTINEL_ECONOMY_MODEL__MODEL"),
    )
    economy_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SENTINEL_ECONOMY_API_KEY", "SENTINEL_ECONOMY_MODEL__API_KEY"
        ),
    )
    economy_temperature: float = Field(
        default=0.2,
        validation_alias=AliasChoices(
            "SENTINEL_ECONOMY_TEMPERATURE", "SENTINEL_ECONOMY_MODEL__TEMPERATURE"
        ),
    )
    economy_max_tokens: int | None = Field(
        default=4096,
        validation_alias=AliasChoices(
            "SENTINEL_ECONOMY_MAX_TOKENS", "SENTINEL_ECONOMY_MODEL__MAX_TOKENS"
        ),
    )

    # P2-3c 用量与预算；0 表示不启用预算或不计价。
    budget_limit: float = Field(default=0.0, ge=0)
    premium_input_price_per_mtok: float = Field(default=0.0, ge=0)
    premium_output_price_per_mtok: float = Field(default=0.0, ge=0)
    economy_input_price_per_mtok: float = Field(default=0.0, ge=0)
    economy_output_price_per_mtok: float = Field(default=0.0, ge=0)

    # 运行时上下文治理：软阈值摘要、硬阈值 checkpoint + reset。
    # 硬阈值上限 0.9 与 AgentScope ContextConfig.trigger_ratio 的约束一致。
    context_monitor_enabled: bool = True
    context_soft_ratio: float = Field(default=0.55, gt=0, lt=1)
    context_hard_ratio: float = Field(default=0.90, gt=0, le=0.9)

    @model_validator(mode="after")
    def _validate_context_ratios(self) -> "Settings":
        if not self.context_soft_ratio < self.context_hard_ratio <= 0.9:
            raise ValueError(
                "context_soft_ratio 必须小于 context_hard_ratio，且硬阈值不超过 0.9；"
                f"当前为 {self.context_soft_ratio} / {self.context_hard_ratio}"
            )
        return self

    @property
    def premium_model(self) -> ModelProfile:
        """优异档（Coder / Tester）。"""
        return ModelProfile(
            base_url=self.premium_base_url,
            model=self.premium_model_id,
            api_key=self.premium_api_key,
            temperature=self.premium_temperature,
            max_tokens=self.premium_max_tokens,
        )

    @property
    def economy_model(self) -> ModelProfile:
        """平价档（Orchestrator / Writer）。"""
        return ModelProfile(
            base_url=self.economy_base_url,
            model=self.economy_model_id,
            api_key=self.economy_api_key,
            temperature=self.economy_temperature,
            max_tokens=self.economy_max_tokens,
        )
