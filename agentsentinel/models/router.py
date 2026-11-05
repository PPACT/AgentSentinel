"""逐角色模型路由：角色 → 档位 → OpenAI 兼容模型实例。

不写死任何供应商：一律使用 AgentScope 的 ``OpenAIChatModel`` +
``OpenAICredential``，由 ``base_url`` + ``model`` 指向任意兼容端点。
真实模式缺配置时 fail-closed，错误信息只列缺失的环境变量名，不回显 Key。
"""

from typing import Any, Literal

from agentscope.credential import OpenAICredential
from agentscope.model import ChatModelBase, OpenAIChatModel

from agentsentinel.config import ModelProfile, Settings

Tier = Literal["premium", "economy"]

# 角色 → 档位；Manager 保持确定性治理，不接模型。
ROLE_TIERS: dict[str, Tier | None] = {
    "orchestrator": "economy",
    "coder": "premium",
    "tester": "premium",
    "writer": "economy",
    "manager": None,
}

TIER_ENV_PREFIX: dict[Tier, str] = {
    "premium": "SENTINEL_PREMIUM",
    "economy": "SENTINEL_ECONOMY",
}


class ModelRouter:
    """按角色解析档位并缓存模型；同档角色复用同一个 client。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[Tier, ChatModelBase] = {}

    def for_role(self, role: str) -> ChatModelBase | None:
        """返回角色对应的模型；``manager`` 不接模型，返回 None。"""
        key = role.casefold()
        if key not in ROLE_TIERS:
            raise ValueError(f"未知角色: {role}")
        tier = ROLE_TIERS[key]
        if tier is None:
            return None
        if tier not in self._cache:
            self._cache[tier] = self._build(tier, self._profile(tier))
        return self._cache[tier]

    def _profile(self, tier: Tier) -> ModelProfile:
        return self._settings.premium_model if tier == "premium" else self._settings.economy_model

    def _build(self, tier: Tier, profile: ModelProfile) -> ChatModelBase:
        prefix = TIER_ENV_PREFIX[tier]
        missing: list[str] = []
        if not profile.model:
            missing.append(f"{prefix}_MODEL")
        if profile.api_key is None:
            missing.append(f"{prefix}_API_KEY")
        if missing:
            raise RuntimeError(
                f"SENTINEL_MODEL_BACKEND=real 但缺少 {' / '.join(missing)}；"
                "请填写 .env.example 或改为 stub。"
            )

        credential_kwargs: dict[str, Any] = {"api_key": profile.api_key}
        if profile.base_url:
            credential_kwargs["base_url"] = profile.base_url
        credential = OpenAICredential(**credential_kwargs)
        parameters = OpenAIChatModel.Parameters(
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
        )
        return OpenAIChatModel(
            credential=credential,
            model=profile.model,
            parameters=parameters,
            stream=False,
        )
