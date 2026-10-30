"""提示词、RoleSpec 与 AgentScope 组合测试（离线）。"""

from collections.abc import AsyncGenerator

from agentscope.agent import Agent
from agentscope.model import ChatModelBase, ChatResponse

from agentsentinel.roles import (
    Coder,
    Manager,
    Orchestrator,
    RoleSpec,
    Tester,
    Writer,
    build_role_agent,
    load_prompt,
)

ROLE_CLASSES = (Orchestrator, Coder, Tester, Writer, Manager)


class StubChatModel(ChatModelBase):
    """只用于构造 Agent，不参与任何推理或网络调用。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def _call_api(
        self, *args: object, **kwargs: object
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        raise NotImplementedError("stub model 不执行任何推理")


def test_each_role_has_a_prompt_with_its_name() -> None:
    for role in ROLE_CLASSES:
        spec = role.spec
        prompt = load_prompt(spec.prompt_name)
        assert spec.name in prompt
        assert "TODO" not in prompt


def test_spec_keeps_prompt_and_tool_whitelist_separate() -> None:
    for role in ROLE_CLASSES:
        spec = role.spec
        assert isinstance(spec, RoleSpec)
        assert spec.system_prompt() == load_prompt(spec.prompt_name)
        assert spec.tools == role.tools


def test_roles_compose_agentscope_agent_without_inheriting() -> None:
    for role in ROLE_CLASSES:
        agent = build_role_agent(role.spec, StubChatModel())

        assert isinstance(agent, Agent)
        assert agent.name == role.name


def test_high_risk_roles_declare_forbidden_tools() -> None:
    assert "git_push" in Orchestrator.spec.forbidden_tools
    assert "git_reset_to_baseline" in Coder.spec.forbidden_tools
    assert "file_write" in Tester.spec.forbidden_tools
    assert Manager.spec.forbidden_tools == ()
