# P2-3 异构模型调度方案（供应商无关）

> 状态：已批准（2026-11-05）；同日按用户纠正改为**供应商无关**设计。
> 范围：配置与路由（P2-3a）+ 专家角色模型驱动接线（P2-3b）合并。
> 不含：P2-3c 用量统计、Watchdog、真实网络测试进入 pytest。

## 1. 决策摘要

- **不写死任何 LLM 供应商**：统一走 OpenAI 兼容协议（`OpenAIChatModel` + 可配置 `base_url`），DeepSeek / Qwen / 本地 vLLM / Ollama / OpenAI 均可接入。
- 只提供两档配置模板：**优异模型（premium）**与**平价模型（economy）**；API Key 在 `.env.example` 中留空，由使用者填写。
- 角色到档位的映射是框架策略，不是供应商绑定：Orchestrator / Writer → economy，Coder / Tester → premium，Manager 不接模型。
- 默认 `model_backend=stub`；`real` 模式缺配置时给出明确报错，不静默猜测端点。
- 不新增依赖，使用 `agentscope[full]` 已内置的 `OpenAIChatModel` / `OpenAICredential`。

## 2. 依赖与协议

拟新增依赖：**无**。

| 能力 | 实现 |
|---|---|
| 模型调用 | `agentscope.model.OpenAIChatModel` |
| 凭证 | `agentscope.credential.OpenAICredential`（`api_key` + 可选 `base_url`） |
| 兼容端点 | 任意 OpenAI Chat Completions 兼容服务：DeepSeek、Qwen/DashScope 兼容模式、vLLM、Ollama、OpenAI 等 |
| 明确不使用 | `DeepSeekChatModel` / `AnthropicChatModel` 等供应商专用类，避免把供应商写进代码 |

> 本地端点通常不校验 Key，可填任意非空占位值（如 `local`）；真实 Key 只放 `.env`，不提交。

## 3. 角色 → 档位映射（框架策略）

| 角色 | 档位 | 说明 |
|---|---|---|
| Orchestrator | economy | 高频分派与状态判断，成本优先 |
| Coder | premium | 代码质量优先 |
| Tester | premium | 独立验证，强推理 |
| Writer | economy | 文档生成 |
| Manager | 不接模型 | 治理与审计保持确定性 |

映射常量放在 `ModelRouter` 内，可读、可测试；不引入 per-role 供应商字段。

## 4. 配置设计（`agentsentinel/config.py`）

```python
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, SecretStr
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
    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_",
        env_file=Path(".env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # 既有字段保持不变；openai_api_key 仅为 legacy 兼容，不参与模型路由
    openai_api_key: SecretStr | None = None
    webhook_secret: SecretStr | None = None
    git_user_name: str = ""
    git_user_email: EmailStr | None = None
    git_remote_url: str = ""

    # P2-3：只有两档，不含供应商字段
    model_backend: Literal["stub", "real"] = "stub"
    premium_model: ModelProfile = ModelProfile(temperature=0.0, max_tokens=8192)
    economy_model: ModelProfile = ModelProfile(temperature=0.2, max_tokens=4096)
```

> 实现落地（`5489f05`）：`Settings` 采用扁平字段 + `AliasChoices`，同时兼容
> `SENTINEL_PREMIUM_MODEL` 与 `SENTINEL_PREMIUM_MODEL__MODEL`；`premium_model` /
> `economy_model` 为只读属性。空字符串/纯空白的 `model` 与 `api_key` 一律按缺失处理。
> `openai_api_key` 仅为 legacy 兼容，不参与路由；`.env.example` 只保留两档模板。

`.env.example`（新增，值留空或占位）：

```env
# stub=离线默认；real=调用 OpenAI 兼容端点
SENTINEL_MODEL_BACKEND=stub

# ===== 优异模型（Coder / Tester）=====
SENTINEL_PREMIUM_BASE_URL=
SENTINEL_PREMIUM_MODEL=
SENTINEL_PREMIUM_API_KEY=
SENTINEL_PREMIUM_TEMPERATURE=0.0
SENTINEL_PREMIUM_MAX_TOKENS=8192

# ===== 平价模型（Orchestrator / Writer）=====
SENTINEL_ECONOMY_BASE_URL=
SENTINEL_ECONOMY_MODEL=
SENTINEL_ECONOMY_API_KEY=
SENTINEL_ECONOMY_TEMPERATURE=0.2
SENTINEL_ECONOMY_MAX_TOKENS=4096
```

规则：

1. `stub`：不读取任何模型配置，零网络，既有 257 条测试不变。
2. `real`：`premium_model` / `economy_model` 的 `model` 与 `api_key` 必须非空；缺失时抛 `RuntimeError`，消息明确列出缺失的环境变量名，例如：
   `SENTINEL_MODEL_BACKEND=real 但缺少 SENTINEL_PREMIUM_MODEL / SENTINEL_PREMIUM_API_KEY；请填写 .env.example 或改为 stub。`
3. `base_url` 可为空（OpenAI 官方端点）；接入 DeepSeek / 本地端点时必须填写。
4. `SecretStr` 保证 repr / 日志不泄露；Key 不写入 audit / state / 任何报告。
5. 嵌套环境变量覆盖需测试：`SENTINEL_PREMIUM_MODEL__MODEL` 等。

## 5. `ModelRouter`（新增 `agentsentinel/models/router.py`）

```python
class ModelRouter:
    def __init__(self, settings: Settings) -> None: ...
    def for_role(self, role: str) -> ChatModelBase | None: ...
```

- 角色 → 档位常量映射；未知角色抛 `ValueError`。
- 档位 → `ModelProfile`；按档位缓存模型实例（同档角色复用 client）。
- 构造：`OpenAICredential(api_key=..., base_url=profile.base_url)` + `OpenAIChatModel(credential=..., model=profile.model, parameters=..., stream=False)`。
- `base_url` 为空时不传，交给 OpenAI 默认端点。
- `parameters` 只传 `temperature` / `max_tokens`。
- 缺配置时不构造任何模型，直接抛清晰的 `RuntimeError`。

## 6. 模型驱动专家角色（P2-3b）

### 6.1 接线

- `SentinelPipeline.__init__` 增 `model_backend: Literal["stub", "real"] = "stub"`、`model_router: ModelRouter | None = None`、`settings: Settings | None = None`。
- 优先级：显式 `model_factory` > `model_backend="real"` 的 router > 默认 `scripted_model`。
- `Orchestrator` / `OrchestrationSession` 增可选 `role_model_for: Callable[[str], ChatModelBase | None] | None`；为 `None` 时专家角色继续走确定性 `handle()`。
- `Coder.handle` / `Tester.handle` / `Writer.handle` 增可选 `model: ChatModelBase | None = None`：
  - `None`：现有确定性桩，行为与测试完全不变。
  - 非 `None`：构造该角色 Agent 并 `run_agent_sync(agent, UserMsg("user", prompt))`。

### 6.2 工作区受限工具（安全边界）

真实模式不得把 AgentScope 内置 `Bash` / `Write` / `Edit` 直接交给专家角色。新增工作区工具适配器：

| 角色 | 工具 | 行为 |
|---|---|---|
| Coder | `file_read(path)` | 只读 `workspace/code/` |
| Coder | `file_write(path, content)` | 只写 `workspace/code/`，记录产物路径 |
| Coder | `file_edit(path, old, new)` | 只改 `workspace/code/` |
| Tester | `file_read(path)` | 只读 `workspace/` |
| Tester | `submit_test_report(status, summary, details)` | 写 `workspace/reports/test-{ts}.json`，返回路径 |
| Writer | `file_read(path)` | 只读 `workspace/` |
| Writer | `file_write(path, content)` | 只写 `workspace/docs/` |
| Writer | `file_edit(path, old, new)` | 只改 `workspace/docs/` |

- 路径经 `Workspace._resolve` 校验，越界抛错。
- Tester 首期不提供 shell；未调用报告工具判 `FAIL`。
- 工具函数需带 docstring 的 `Args`，供 `FunctionTool` 生成 schema。
- 若沿用 `build_toolkit`，需让 `extra_tools` 同名实现优先于内置工具；或新增专用 builder。不得放宽 `RoleSpec.forbidden_tools`。

### 6.3 结果转换

- Agent 回复文本作为 `AgentResult.summary`；Writer 截断至 200 字。
- 产物：文件工具记录的实际写入路径；Tester 用报告路径。
- Coder 未写任何文件视为失败；Tester 未提交报告视为 `FAIL`。
- 异常不吞：模型调用/工具异常向上抛，由编排按失败处理并写状态流。

## 7. 测试与冒烟

离线测试（禁止真实 Key / 外网）：

1. `tests/test_model_config.py`：默认 stub；`real` 缺 Key 的报错包含缺失变量名；嵌套环境变量覆盖；`repr` 不含 Key。
2. `tests/test_model_router.py`：角色→档位映射；同档复用实例；未知角色抛错；`base_url` 为空时不传；构造参数正确。用 monkeypatch 替换 `OpenAIChatModel`，不触网。
3. `tests/roles/test_model_driven_roles.py`：用 `StubChatModel` 发脚本化工具调用，验证 Coder/Writer/Tester 的工作区工具、越界拒绝、未写文件/未交报告判失败。
4. 既有 257 条测试保持通过（交付时 pytest 283 passed，新增 26 条）。

实现补充（`5489f05`）：`run_agent_sync` 在已有事件循环时改用独立线程开新循环，
以支撑编排 Agent 的异步工具内同步调用专家模型。该路径与按档缓存的
`OpenAIChatModel` 跨 loop 复用 client 的风险已记录，需在真实端点冒烟时重点验证。

冒烟（不进 pytest）：

- 提供 `scripts/smoke_model_router.py`：读取两档配置，打印实际使用的 `base_url` / `model`，发一次最小请求；配置缺失时打印清晰提示并以非零码退出。
- 可用 DeepSeek 或任意本地 OpenAI 兼容端点（Ollama / vLLM）执行；不得把真实 Key 写入仓库。
- 若当前环境无可用端点，回执注明「冒烟未执行，原因：无可用端点」，不伪造结果。

## 8. 验收与约束

验收：

- `uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy .`、`uv run pytest` 均 exit 0。
- 回执附 pytest 计数与新增用例清单。
- 推送前扫描 `results: {}`；提交后 `git status --short` 为空。

约束：

- 不新增依赖；不改 `README.md`、`agentsentinel/api/**`、`.claude/**`。
- 不改 `AuditRecord` / `StateRecord` 字段与审计 reason。
- 不在代码里出现具体供应商名或写死模型 ID；`.env.example` 只留模板与占位。
- 不把 Key 写入任何文件、日志、状态流、审计。
- 默认 `stub` 路径行为与性能不变。

## 9. 风险

- 所有供应商统一走 OpenAI 兼容协议；若某模型不支持 Chat Completions 协议，需要中间代理，首期不覆盖。
- 专家角色首次模型驱动，提示词与工具 schema 需要冒烟调优；离线测试只证明接线，不证明模型质量。
- 真实模型上下文膨胀与 Token 成本尚无预算闸门（P2-3c）。
- 不同兼容端点的参数支持略有差异；构造时只传通用字段。
