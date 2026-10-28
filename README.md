<div align="center">

# 🛡️ AgentSentinel

**A Secure, Governed, and Self-Healing Multi-Agent Development Pipeline.**

**安全受控、支持版本回退的多智能体研发流水线**

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![AgentScope](https://img.shields.io/badge/Framework-AgentScope-orange.svg?style=flat-square)](https://github.com/modelscope/agentscope)
[![Security](https://img.shields.io/badge/Security-Data%20Sanitization-red.svg?style=flat-square)](#)
[![Audit](https://img.shields.io/badge/Governance-Audit%20Trail-green.svg?style=flat-square)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)

</div>

---

## 📖 简介 (Introduction)

**AgentSentinel** 是一套面向企业级研发场景的多智能体（Multi-Agent）协作系统。针对目前大模型 Agent 在真实业务中常遇到的“上下文污染”、“工具越权”和“敏感数据泄露”等痛点，本系统引入了 **主管编排 (Supervisor)** 架构与 **运行时权限拦截 (Permission Interception)** 机制。

系统内置 5 个专业角色 Agent，覆盖从需求规划、代码编写、自动化测试、文档撰写到代码仓库推送的完整生命周期，并提供**不可绕过的脱敏扫描**与**版本原子级回退**能力。

## ✨ 核心特性 (Features)

- 🧠 **主管编排模式 (Supervisor Pattern)**：主编排 Agent 统筹全局，专家 Agent 独立执行，上下文物理隔离，避免长任务带来的上下文溢出。
- 🚫 **不可绕过的脱敏拦截**：代码与文档在推送前强制经过静态扫描，基于底层权限系统（ALLOW/DENY/ASK），一旦发现敏感信息即刻终止，**拒绝自行重试绕过**。
- 🔄 **版本回退与闭环反馈**：测试失败自动反馈至代码 Agent；严重故障时，由主编排一键触发仓库版本回退（Revert to Baseline）。
- 📝 **全链路审计日志**：每次操作生成结构化记录（`audit/push-log.jsonl`），确保每一次推送、拦截和回退都有迹可循。
- 🛠️ **基于 AgentScope 构建**：利用共享 Workspace 交换产物，天然支持大负载上下文卸载（Offloading）。

---

## 🏗️ 系统架构 (Architecture)

本项目采用 **TeamPipeline**（队长-成员）模式。主编排 Agent 作为中枢，通过共享工作区与工具调用调度四大专家。

```mermaid
graph TD
    User([用户/需求方]) -->|任务下发| Orchestrator[🧠 主编排 Agent<br/>Orchestrator]

    subgraph "Agent Pipeline (共享工作区)"
        Orchestrator -->|分配任务| Coder[💻 代码 Agent<br/>Coder]
        Orchestrator -->|分配任务| Tester[🧪 测试 Agent<br/>Tester]
        Orchestrator -->|分配任务| Writer[📝 文档 Agent<br/>Writer]
        Orchestrator -->|分配任务| Manager[🛡️ 管理 Agent<br/>Manager]

        Coder -.->|写入 diff| SharedWorkspace[(📁 Shared Workspace)]
        Tester -.->|读取 & 写入报告| SharedWorkspace
        Writer -.->|读取 & 写入文档| SharedWorkspace
        Manager -.->|读取产物| SharedWorkspace
    end

    subgraph "安全治理层 (Governance Layer)"
        Manager -->|1. 触发扫描| Sanitizer{🔍 脱敏拦截器<br/>Sanitize Scan}
        Sanitizer -->|ALLOW| GitPush[🚀 Git Push / Tag]
        Sanitizer -->|DENY| Block[⛔ 阻断并回传错误]
        Manager -->|2. 异常回退| GitReset[⏪ Reset to Baseline]
        GitPush --> AuditLog[(📋 审计日志)]
        GitReset --> AuditLog
    end

    GitPush --> RemoteRepo([🌐 远程代码仓库])
```

---

## 🤖 角色矩阵 (Agent Matrix)

| Agent 角色 | 核心职责 | 关键技能 / 工具 (Skills) | 权限管控要点 |
| :--- | :--- | :--- | :--- |
| **Orchestrator** <br/>🧠 主编排 | 拆解需求、分派任务、全局状态维护、触发回退 | `TeamAssign`, `query_task_status` | 禁止直接执行代码或 Git 操作 |
| **Coder** <br/>💻 代码专家 | 根据需求实现代码、根据测试报告修复 Bug | `file_edit`, `bash` (仅限构建/语法检查) | 禁止执行 `git` 命令 |
| **Tester** <br/>🧪 测试专家 | 独立验证代码产物、输出结构化测试报告 | `bash` (执行测试), `regression-suite` | 必须与 Coder 上下文隔离，防自我评估偏差 |
| **Writer** <br/>📝 文档专家 | 根据代码 Diff 更新技术文档、生成变更摘要 | `file_write`, `doc-consistency-check` | 必须明确反映已通过测试的实际变更 |
| **Manager** <br/>🛡️ 管理专家 | 脱敏扫描、仓库推送、打基线、版本回退、写审计日志 | `sanitize_scan`, `git_push`, `git_reset_to_baseline` | 高危工具强制设为 `ASK`，`force push` 设为 `DENY` |

---

## 🚀 快速开始 (Quick Start)

### 1. 环境准备

本项目推荐使用 **Python 3.12+**，并基于 **AgentScope 2.0.6** 构建。推荐使用 `uv` 作为包管理器以提升依赖解析速度。

```bash
# 克隆仓库
git clone https://github.com/your-username/AgentSentinel.git
cd AgentSentinel

# 安装依赖
uv pip install -r requirements.txt
# 或使用 pip
pip install -r requirements.txt
```

### 2. 环境变量配置

请在项目根目录创建 `.env` 文件，配置大模型 API Key 及 Git 凭证：

```env
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxx
GIT_USER_NAME="AgentSentinel Bot"
GIT_USER_EMAIL="bot@agentsentinel.local"
GIT_REMOTE_URL="https://github.com/your-org/target-repo.git"
```

### 3. 启动流水线

```python
# main.py (示例)
from agentsentinel.pipeline import TeamPipeline
from agentsentinel.agents import Orchestrator, Coder, Tester, Writer, Manager

# 初始化各个 Agent (已绑定 Workspace 和 Permission)
pipeline = TeamPipeline(
    leader=Orchestrator(),
    members=[Coder(), Tester(), Writer(), Manager()]
)

# 下发研发任务
task_prompt = "请开发一个用户登录接口，包含 JWT 鉴权，并补充单元测试和相关文档。"
pipeline.run(task_prompt)
```

---

## 📦 核心依赖 (Dependencies)

项目围绕“安全治理”与“研发交付”场景，显式引入了以下核心依赖（详见 `requirements.txt`）：

```txt
# ===== 核心框架 =====
agentscope[full]==2.0.6

# ===== 治理层（本项目特有） =====
detect-secrets==1.5.0    # 脱敏扫描规则引擎
GitPython>=3.1.42        # Git 操作封装 (Push/Tag/Reset)
pydantic-settings>=2.9.1 # 配置与契约校验
python-dotenv>=1.0.1     # 环境变量加载
structlog>=25.4.0        # 结构化审计日志输出
```

---

## 🔐 安全与治理 (Security & Governance)

本项目坚决贯彻 **“安全即代码 (Security as Code)”** 的理念，不依赖 LLM 的自我约束：

1. **硬性脱敏拦截**：`sanitize_scan` 工具在 `checkPermissions` 阶段进行运行时正则匹配。若检出敏感信息，抛出 `DENY`，此状态为**业务终态**，严禁 Agent 自动重试。
2. **调用者身份校验**：`git_reset_to_baseline` 等回退指令，需经过严格的 Caller Identity 校验，仅限 Orchestrator 可调用。
3. **审计追踪**：每一次推送、拦截、回退均写入不可篡改的本地 JSONL 日志。

---

## 🤝 贡献与致谢 (Contributing)

- 欢迎提交 Issue 或 Pull Request 优化 Agent 提示词与工作流。
- 本项目架构灵感来源于 [AgentScope](https://github.com/modelscope/agentscope) 的 TeamPipeline 与内置权限系统。

<div align="center">

<i>Built with ❤️ for Secure Agentic Workflows.</i>

</div>
