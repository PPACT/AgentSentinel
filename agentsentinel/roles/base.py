"""Agent 角色接口、提示词加载与 AgentScope 组合。

角色用组合而非继承：RoleSpec（name + prompt + 工具白名单）+ 可选 Agent 绑定。
M3 只固定接口与离线行为；真实模型在 M4 编排时注入。
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from agentscope.agent import Agent
from agentscope.model import ChatModelBase

from agentsentinel.contracts import TestStatus
from agentsentinel.workspace import Workspace

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


class PipelineOutcome(StrEnum):
    """一次编排的最终结果。"""

    PASSED = "passed"
    FAILED = "failed"
    DENIED = "denied"


@dataclass(frozen=True)
class RoleSpec:
    """角色配置：提示词与工具白名单分离存放。"""

    name: str
    prompt_name: str
    tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...] = ()

    def system_prompt(self) -> str:
        return load_prompt(self.prompt_name)


@dataclass(frozen=True)
class AgentResult:
    """角色执行结果；summary 用于回传主编排，避免携带大体积产物。"""

    agent: str
    summary: str
    artifacts: tuple[Path, ...] = ()
    status: TestStatus | None = None


@dataclass(frozen=True)
class OrchestrationResult:
    """主编排对一次任务的汇总结果。"""

    status: PipelineOutcome
    summary: str
    artifacts: tuple[Path, ...] = ()
    attempts: int = 1


class AgentRole(Protocol):
    """专家角色的统一接口。"""

    name: str
    tools: tuple[str, ...]
    spec: RoleSpec

    def handle(self, task: str, workspace: Workspace, *, feedback: str = "") -> AgentResult: ...

    def build_agent(self, model: ChatModelBase) -> Agent: ...


def load_prompt(name: str) -> str:
    """读取 agentsentinel/prompts/<name>.md；缺失即报错，不回退到空提示词。"""
    path = PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"缺少角色提示词: {path}")
    return path.read_text(encoding="utf-8")


def build_role_agent(spec: RoleSpec, model: ChatModelBase) -> Agent:
    """组合 Agent + 提示词 + 模型；工具白名单由 M4 的 Toolkit 绑定。"""
    return Agent(name=spec.name, system_prompt=spec.system_prompt(), model=model)
