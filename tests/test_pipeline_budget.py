"""P2-3c 流水线预算：超限返回 FAILED，不推送且不再调用模型。"""

import json
from pathlib import Path
from typing import Any

from agentscope.credential import CredentialBase
from agentscope.message import Msg
from agentscope.model import ChatModelBase, ChatResponse, ChatUsage

from agentsentinel.config import Settings
from agentsentinel.pipeline import SentinelPipeline
from agentsentinel.roles import PipelineOutcome, text_reply, tool_call
from agentsentinel.roles.runtime import StubFormatter
from agentsentinel.workspace import Workspace


class _BudgetFakeModel(ChatModelBase):
    """首次调用下发 Coder 分派，之后返回文本；每次响应都带高额用量。"""

    def __init__(self, usage: ChatUsage) -> None:
        super().__init__(
            credential=CredentialBase(),
            model="fake-budget-model",
            parameters=ChatModelBase.Parameters(),
            stream=False,
        )
        self.formatter = StubFormatter()
        self._usage = usage
        self.calls = 0

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls += 1
        if self.calls == 1:
            response = tool_call("TeamAssign", {"role": "Coder", "task": "demo"})
        else:
            response = text_reply("done")
        response.usage = self._usage
        return response


class _FakeRouter:
    def __init__(self, model: ChatModelBase) -> None:
        self._model = model

    def for_role(self, role: str) -> ChatModelBase | None:
        return None if role.casefold() == "manager" else self._model


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        budget_limit=1.0,
        premium_input_price_per_mtok=1.0,
        premium_output_price_per_mtok=0.0,
        economy_input_price_per_mtok=1.0,
        economy_output_price_per_mtok=0.0,
    )


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_budget_exceeded_returns_failed_without_push(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    model = _BudgetFakeModel(ChatUsage(input_tokens=2_000_000, output_tokens=0, time=0.1))
    pipeline = SentinelPipeline(
        workspace,
        model_backend="real",
        model_router=_FakeRouter(model),  # type: ignore[arg-type]
        settings=_settings(),
    )

    result = pipeline.run("demo task")

    assert result.status is PipelineOutcome.FAILED
    assert "预算熔断" in result.summary
    assert model.calls == 1
    assert not result.audit_log.exists()

    rows = _read_rows(workspace.usage_path)
    assert rows[0]["role"] == "orchestrator"
    assert rows[0]["tier"] == "economy"
    assert rows[0]["exceeded"] is False
    assert rows[0]["estimated_cost"] == 2.0
    assert rows[-1]["exceeded"] is True
    assert rows[-1]["budget_limit"] == 1.0
