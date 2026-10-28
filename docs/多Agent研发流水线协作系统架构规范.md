# 多Agent研发流水线协作系统架构规范 (Agent Pipeline Spec)

## 1\. 总体架构

* **架构模式**：Supervisor（主管/队长模式） -> `TeamPipeline`
* **通信机制**：主编排 Agent 作为队长，通过工具调用分派任务；各专家 Agent 在独立上下文中执行，成员之间不直接对话。
* **数据交换**：共享 Workspace（工作区），通过读写文件交换产物（Artifact Passing）。
* **框架底座**：AgentScope（依赖其 Permission 拦截、Workspace 机制和 TeamPipeline）。

## 2\. Agent 角色与职责定义

### 2.1 主编排 Agent (Orchestrator)

* **角色**：研发流水线的指挥官。
* **职责**：拆解任务、路由分派、验收产物、维护全局状态、触发回退。
* **约束**：

  * **禁止**直接编写代码或执行 Git 操作。
  * 必须配置上下文卸载（Offloading），大体积产物只保留摘要和文件路径。
  * 只有在测试 Agent 返回 `PASS` 后，才能让管理 Agent 推送。
  * 若测试连续失败两次，必须触发管理 Agent 执行回退。
* **核心工具/Skill**：

  * `TeamAssign`: (框架内置) 分派任务给成员。
  * `query\_task\_status`: 从 Workspace 读取审计日志，查询当前流水线状态。

### 2.2 代码 Agent (Coder)

* **角色**：代码实现者。
* **职责**：根据任务描述产出或修改代码。
* **约束**：

  * 产物必须写入 `workspace/code/` 目录。
  * **禁止**执行 `git` 相关命令。
  * 收到测试失败报告时，基于 diff 进行局部修正，不要全量重写。
* **核心工具/Skill**：

  * `file\_write`, `file\_edit`: 写入/修改文件。
  * `bash`: 受限执行，仅允许运行构建和语法检查（如 `npm run build`, `python -m py\_compile`）。

### 2.3 测试 Agent (Tester)

* **角色**：质量守门员。
* **职责**：执行测试用例，验证代码产物，输出结构化测试报告。
* **约束**：

  * 必须与代码 Agent 上下文物理隔离。
  * 默认假设代码有 Bug，不可轻信代码 Agent 的自评。
  * 必须输出严格的 JSON 格式测试报告至 `workspace/reports/test-{timestamp}.json`。
* **核心工具/Skill**：

  * `file\_read`: 读取代码和测试用例。
  * `bash`: 执行测试脚本（如 `pytest`, `npm test`）。
  * `Skill: regression-suite`: 维护必须执行的最小回归测试清单。

### 2.4 文档 Agent (Writer)

* **角色**：文档维护者。
* **职责**：根据代码 diff 和测试报告同步更新文档。
* **约束**：

  * 产物写入 `workspace/docs/` 目录。
  * 向主编排汇报时，必须附带不超过 200 字的变更摘要及文件路径。
* **核心工具/Skill**：

  * `file\_read`, `file\_write`, `file\_edit`。
  * `Skill: doc-consistency-check`: 校验文档接口描述与代码签名是否一致。

### 2.5 管理 Agent (Manager)

* **角色**：仓库安全与运维。
* **职责**：脱敏扫描、推送代码/文档、打版本基线、执行版本回退、记录审计日志。
* **约束**：

  * `\[MUST]` 推送前必须执行 `sanitize\_scan`，若返回 `DENY`，**立即终态终止**，禁止重试或绕过。
  * `\[MUST]` 回退工具仅接受主编排 Agent 的调用。
  * `\[MUST]` 每次操作必须写入 `workspace/audit/push-log.jsonl` 审计日志。
* **核心工具/Skill**：

  * `sanitize\_scan`: 自定义工具，在 `checkPermissions` 中实现敏感信息（API Key、IP、密码等）拦截。
  * `git\_push`, `git\_commit`: 权限模式配置为 `ASK` 或 `DEFAULT`。
  * `git\_tag\_baseline`: 推送前打轻量 tag 作为回退锚点。
  * `git\_reset\_to\_baseline`: 仅主编排可调用的回退工具。
  * `audit\_logger`: 写入操作日志。

## 3\. 系统治理与权限配置 (Governance)

### 3.1 脱敏拦截规范

* **实现位置**：管理 Agent 的 `sanitize\_scan` 工具内置的 `checkPermissions` 检查中，**不可依赖提示词自我约束**。
* **拦截逻辑**：

  * 匹配正则：API Keys, 内网 IP, 密码特征, 身份证号等。
  * 返回 `DENY` 时，将拒绝原因回传给模型，强制模型重新生成内容或人工介入。

### 3.2 权限模式矩阵

* **主编排 / 代码 / 测试 / 文档 Agent**：使用 `ACCEPT\_EDITS` 或 `DEFAULT`。
* **管理 Agent**：使用 `DEFAULT` 或 `DONT\_ASK`。**禁止使用 `BYPASS` 模式**。

### 3.3 状态与审计日志 (Audit Trail)

* **日志路径**：`workspace/audit/push-log.jsonl`
* **记录字段**：`timestamp`, `caller`, `commit\_hash`, `scan\_result`, `test\_result`, `action\_type` (push/revert)。

## 4\. 协作工作流 (Workflow)

1. **主编排** 接收需求，分派给 **代码 Agent** 和 **文档 Agent**。
2. **代码 Agent** 将代码写入 `workspace/code/`。
3. **主编排** 分派 **测试 Agent** 验证代码。
4. **测试 Agent** 执行测试，若失败，退回主编排，主编排将错误信息分派给代码 Agent 修复；若通过，进入下一步。
5. **主编排** 分派 **文档 Agent** 更新文档，文档 Agent 提交变更摘要给主编排。
6. **主编排** 确认测试通过后，指令 **管理 Agent** 执行推送。
7. **管理 Agent** 触发脱敏扫描：

   * 若 `DENY`：终止推送，报告主编排。
   * 若 `ALLOW`：打 Tag 基线 -> 执行 `git push` -> 写审计日志。
8. **异常处理**：若在生产环节发现严重错误，主编排指令管理 Agent 执行 `git\_reset\_to\_baseline` 回退，并记录审计日志。

## 5\. 关键技术风险规避

* **防上下文爆炸**：主编排必须启用 Workspace 的 payload 卸载能力，避免长篇代码或日志撑爆上下文。
* **防自动重试绕过**：管理 Agent 的重试机制必须与脱敏拦截器解耦。`DENY` 属于业务终态，不触发网络层的自动重试。
* **防越权回退**：回退工具需要做调用者鉴权（Caller Identity Check），拒绝非主编排 Agent 的调用。

\---

