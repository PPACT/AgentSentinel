"""P2-3c UsageLedger：成本计算、JSONL 字段、预算熔断与计价 fail-closed。"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from agentscope.credential import CredentialBase
from agentscope.message import Msg, TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatModelBase, ChatResponse, ChatUsage

from agentsentinel.config import Settings
from agentsentinel.roles.runtime import StubFormatter
from agentsentinel.usage import BudgetExceededError, MeteredChatModel, UsageLedger


def _settings(**overrides: float) -> Settings:
    values: dict[str, Any] = {
        "budget_limit": 0.0,
        "premium_input_price_per_mtok": 2.0,
        "premium_output_price_per_mtok": 8.0,
        "economy_input_price_per_mtok": 0.5,
        "economy_output_price_per_mtok": 2.0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class _FakeChatModel(ChatModelBase):
    """非流式假模型：固定返回同一响应，只记录调用次数。"""

    def __init__(self, response: ChatResponse, *, stream: bool = False) -> None:
        super().__init__(
            credential=CredentialBase(),
            model="fake-model",
            parameters=ChatModelBase.Parameters(),
            stream=stream,
        )
        self.formatter = StubFormatter()
        self._response = response
        self.calls = 0

    @classmethod
    def _get_retryable_exceptions(cls) -> tuple[type[Exception], ...]:
        return (ValueError,)

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls += 1
        return self._response


def test_record_computes_cost_and_writes_all_fields(tmp_path: Path) -> None:
    log_path = tmp_path / "reports" / "usage.jsonl"
    ledger = UsageLedger(_settings(), log_path, run_id="run-0001")

    ledger.record(
        "coder",
        "premium",
        "model-x",
        ChatUsage(input_tokens=1_000_000, output_tokens=500_000, time=0.4),
    )

    rows = _read_rows(log_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == "run-0001"
    assert row["role"] == "coder"
    assert row["tier"] == "premium"
    assert row["model"] == "model-x"
    assert row["input_tokens"] == 1_000_000
    assert row["output_tokens"] == 500_000
    assert row["cache_input_tokens"] == 0
    assert row["cache_creation_input_tokens"] == 0
    assert row["estimated_cost"] == pytest.approx(2.0 + 4.0)
    assert row["cumulative_cost"] == pytest.approx(6.0)
    assert row["budget_limit"] == 0.0
    assert row["exceeded"] is False
    assert row["usage_available"] is True
    assert datetime.fromisoformat(row["timestamp"]).tzinfo is not None
    assert ledger.total_cost == pytest.approx(6.0)


def test_missing_usage_is_recorded_as_zero(tmp_path: Path) -> None:
    log_path = tmp_path / "usage.jsonl"
    ledger = UsageLedger(_settings(), log_path)

    ledger.record("writer", "economy", "model-y", None)

    (row,) = _read_rows(log_path)
    assert row["input_tokens"] == 0
    assert row["output_tokens"] == 0
    assert row["estimated_cost"] == 0.0
    assert row["cumulative_cost"] == 0.0
    assert row["usage_available"] is False


def test_check_raises_and_logs_exceeded_record(tmp_path: Path) -> None:
    log_path = tmp_path / "usage.jsonl"
    ledger = UsageLedger(_settings(budget_limit=1.0), log_path)
    ledger.record(
        "coder",
        "premium",
        "model-x",
        ChatUsage(input_tokens=1_000_000, output_tokens=0, time=0.1),
    )

    with pytest.raises(BudgetExceededError):
        ledger.check("coder", "premium", "model-x")

    rows = _read_rows(log_path)
    assert [row["exceeded"] for row in rows] == [False, True]
    exceeded = rows[-1]
    assert exceeded["role"] == "coder"
    assert exceeded["input_tokens"] == 0
    assert exceeded["estimated_cost"] == 0.0
    assert exceeded["cumulative_cost"] == pytest.approx(2.0)
    assert exceeded["budget_limit"] == 1.0
    assert exceeded["usage_available"] is False


def test_check_passes_below_limit(tmp_path: Path) -> None:
    ledger = UsageLedger(_settings(budget_limit=10.0), tmp_path / "usage.jsonl")
    ledger.record(
        "coder",
        "premium",
        "model-x",
        ChatUsage(input_tokens=1_000_000, output_tokens=0, time=0.1),
    )

    ledger.check("coder", "premium", "model-x")

    assert ledger.total_cost == pytest.approx(2.0)


def test_budget_enabled_without_prices_fails_closed(tmp_path: Path) -> None:
    log_path = tmp_path / "usage.jsonl"
    ledger = UsageLedger(
        _settings(
            budget_limit=1.0,
            premium_input_price_per_mtok=0.0,
            premium_output_price_per_mtok=0.0,
        ),
        log_path,
    )

    with pytest.raises(RuntimeError, match="SENTINEL_PREMIUM_INPUT_PRICE_PER_MTOK") as error:
        ledger.check("coder", "premium")

    assert "SENTINEL_PREMIUM_OUTPUT_PRICE_PER_MTOK" in str(error.value)
    assert not log_path.exists()


def test_budget_disabled_ignores_zero_prices(tmp_path: Path) -> None:
    ledger = UsageLedger(
        _settings(
            budget_limit=0.0,
            economy_input_price_per_mtok=0.0,
            economy_output_price_per_mtok=0.0,
        ),
        tmp_path / "usage.jsonl",
    )

    ledger.check("writer", "economy")


def test_metered_model_passes_response_through_and_records_usage(tmp_path: Path) -> None:
    response = ChatResponse(
        content=[
            TextBlock(text="ok"),
            ToolCallBlock(id="call-1", name="TeamAssign", input="{}"),
        ],
        is_last=True,
        usage=ChatUsage(input_tokens=1_000_000, output_tokens=250_000, time=0.2),
    )
    inner = _FakeChatModel(response)
    log_path = tmp_path / "usage.jsonl"
    ledger = UsageLedger(_settings(), log_path)
    model = MeteredChatModel(inner, role="coder", tier="premium", ledger=ledger)

    result = asyncio.run(model([UserMsg("user", "hi")]))

    assert result is response
    assert inner.calls == 1
    assert model._get_retryable_exceptions() == (ValueError,)
    (row,) = _read_rows(log_path)
    assert row["role"] == "coder"
    assert row["tier"] == "premium"
    assert row["model"] == "fake-model"
    assert row["estimated_cost"] == pytest.approx(2.0 + 2.0)


def test_metered_model_rejects_streaming_inner() -> None:
    inner = _FakeChatModel(ChatResponse(content=[], is_last=True), stream=True)
    ledger = UsageLedger(_settings(), "unused.jsonl")

    with pytest.raises(RuntimeError, match="流式"):
        MeteredChatModel(inner, role="coder", tier="premium", ledger=ledger)
