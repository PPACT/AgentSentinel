"""用量与预算：按角色计量 Token、估算成本并在超限前熔断。

依据 ``docs/local/p2-3c-usage-budget.md``。记录写入
``workspace/reports/usage.jsonl``，与审计日志、状态流相互独立。

计价口径：单价均按「每百万 Token」计，货币单位由使用者自行保持一致。
``budget_limit=0`` 表示不启用预算；未启用时仍记账，只是不做熔断。
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from agentscope.message import Msg
from agentscope.model import ChatModelBase, ChatResponse, ChatUsage
from agentscope.tool import ToolChoice

from agentsentinel.config import Settings
from agentsentinel.contracts import UsageRecord

Tier = Literal["premium", "economy"]

_INPUT_PRICE_FIELDS: dict[Tier, tuple[str, str]] = {
    "premium": (
        "SENTINEL_PREMIUM_INPUT_PRICE_PER_MTOK",
        "SENTINEL_PREMIUM_OUTPUT_PRICE_PER_MTOK",
    ),
    "economy": (
        "SENTINEL_ECONOMY_INPUT_PRICE_PER_MTOK",
        "SENTINEL_ECONOMY_OUTPUT_PRICE_PER_MTOK",
    ),
}


class BudgetExceededError(RuntimeError):
    """调用前检查发现预算已用尽；流水线据此返回 FAILED，不重试。"""


class UsageLedger:
    """单次流水线运行的用量账本：线程安全累计并追加 JSONL。"""

    def __init__(
        self,
        settings: Settings,
        log_path: Path | str,
        run_id: str | None = None,
    ) -> None:
        self._log_path = Path(log_path)
        self._run_id = run_id if run_id is not None else uuid.uuid4().hex[:8]
        self._budget_limit = settings.budget_limit
        self._prices: dict[Tier, tuple[float, float]] = {
            "premium": (
                settings.premium_input_price_per_mtok,
                settings.premium_output_price_per_mtok,
            ),
            "economy": (
                settings.economy_input_price_per_mtok,
                settings.economy_output_price_per_mtok,
            ),
        }
        self._total_cost = 0.0
        self._lock = threading.Lock()

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def log_path(self) -> Path:
        return self._log_path

    @property
    def total_cost(self) -> float:
        with self._lock:
            return self._total_cost

    def check(self, role: str, tier: Tier, model: str = "") -> None:
        """调用前检查；超限写一条 ``exceeded=true`` 记录并抛错。

        预算启用但档位两项单价均为 0 时 fail-closed：抛 ``RuntimeError``，
        错误信息只列环境变量名，不回显任何密钥。
        """
        with self._lock:
            if self._budget_limit <= 0:
                return
            input_price, output_price = self._prices[tier]
            if input_price == 0 and output_price == 0:
                input_var, output_var = _INPUT_PRICE_FIELDS[tier]
                raise RuntimeError(
                    f"预算已启用但 {tier} 档位缺少计价配置：{input_var} / {output_var}"
                )
            if self._total_cost >= self._budget_limit:
                self._append(
                    UsageRecord(
                        timestamp=datetime.now(UTC),
                        run_id=self._run_id,
                        role=role,
                        tier=tier,
                        model=model,
                        input_tokens=0,
                        output_tokens=0,
                        estimated_cost=0.0,
                        cumulative_cost=self._total_cost,
                        budget_limit=self._budget_limit,
                        exceeded=True,
                        usage_available=False,
                    )
                )
                raise BudgetExceededError(
                    f"预算超限：累计 {self._total_cost:.6g} ≥ 上限 {self._budget_limit:.6g}"
                )

    def record(
        self,
        role: str,
        tier: Tier,
        model: str,
        usage: ChatUsage | None,
    ) -> None:
        """记录一次调用；供应商未返回用量时记零值并标记 ``usage_available=false``。"""
        with self._lock:
            if usage is None:
                input_tokens = 0
                output_tokens = 0
                cache_input_tokens = 0
                cache_creation_input_tokens = 0
                usage_available = False
            else:
                input_tokens = usage.input_tokens
                output_tokens = usage.output_tokens
                cache_input_tokens = usage.cache_input_tokens
                cache_creation_input_tokens = usage.cache_creation_input_tokens
                usage_available = True
            input_price, output_price = self._prices[tier]
            estimated_cost = (
                input_tokens / 1_000_000 * input_price + output_tokens / 1_000_000 * output_price
            )
            self._total_cost += estimated_cost
            self._append(
                UsageRecord(
                    timestamp=datetime.now(UTC),
                    run_id=self._run_id,
                    role=role,
                    tier=tier,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_input_tokens=cache_input_tokens,
                    cache_creation_input_tokens=cache_creation_input_tokens,
                    estimated_cost=estimated_cost,
                    cumulative_cost=self._total_cost,
                    budget_limit=self._budget_limit,
                    exceeded=False,
                    usage_available=usage_available,
                )
            )

    def _append(self, record: UsageRecord) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{record.model_dump_json()}\n")
            stream.flush()


class MeteredChatModel(ChatModelBase):
    """包装真实模型，在每次 ``_call_api`` 前后计量；不改变调用语义。"""

    def __init__(
        self,
        inner: ChatModelBase,
        *,
        role: str,
        tier: Tier,
        ledger: UsageLedger,
    ) -> None:
        if inner.stream:
            raise RuntimeError("MeteredChatModel 不支持流式模型（inner.stream=True）")
        super().__init__(
            credential=inner.credential,
            model=inner.model,
            parameters=inner.parameters,
            stream=False,
            max_retries=inner.max_retries,
            retry_delay=inner.retry_delay,
            context_size=inner.context_size,
        )
        formatter = getattr(inner, "formatter", None)
        if formatter is None:
            raise RuntimeError("MeteredChatModel 要求内层模型提供 formatter")
        self.formatter = formatter
        self._inner = inner
        self._role = role
        self._tier = tier
        self._ledger = ledger

    def _get_retryable_exceptions(self) -> tuple[type[Exception], ...]:  # type: ignore[override]
        """委托内层模型，保持供应商异常的重试语义不变。"""
        return type(self._inner)._get_retryable_exceptions()

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self._ledger.check(self._role, self._tier, self.model)
        response = await self._inner._call_api(
            model_name,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs,
        )
        if not isinstance(response, ChatResponse):
            raise TypeError("MeteredChatModel 仅支持非流式响应")
        self._ledger.record(self._role, self._tier, self.model, response.usage)
        return response
