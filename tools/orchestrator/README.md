# 编排服务 daemon（开发期工具）

> 仅开发期使用，**不接入产品** `agentsentinel/`。产品入口与依赖不受影响。

## 它是什么

`tools/orchestrator/service.py` 是一个常驻本机进程：

- 持有 worker 的 Windows Job 句柄（`KILL_ON_JOB_CLOSE`），保证 worker/子进程成组回收；
- 维护 `.claude/orchestrator/registry.sqlite3`（agents / tasks / messages / steps / effects）；
- 跑监控（ACK 超时、心跳失联、worker 退出、启动恢复）；
- 在 `127.0.0.1` 上提供带 token 的 HTTP 端点，CLI 与 MCP server 都是客户端。

**必须由外部终端常驻启动**：从 agent 的 Bash 里启动的 detached daemon 会被运行环境回收。

## 启动 / 停止

PowerShell：

```powershell
# 前台常驻（推荐；Ctrl+C 停止并回收 worker）
.\scripts\start_orchestrator.ps1 -Action foreground

# 另开终端查看 / 停止
.\scripts\start_orchestrator.ps1 -Action status
.\scripts\start_orchestrator.ps1 -Action stop
```

cmd.exe：

```bat
rem 无参数：前台中控面板（停止后窗口保留）
scripts\start_orchestrator.cmd

rem 带参数：原样转发给 start_orchestrator.ps1
scripts\start_orchestrator.cmd -Action foreground
scripts\start_orchestrator.cmd -Action start
scripts\start_orchestrator.cmd -Action status
scripts\start_orchestrator.cmd -Action stop
```

双击/无参数运行 `.cmd` 进入前台中控：先打印「正在启动编排中控...」，随后显示横幅与 agent 表；Ctrl+C 或异常退出后打印「编排中控已停止」，窗口保留（按任意键关闭）。显式 `-Action start` 仍是 detached 后台启动并等待 health；`-Action status/stop` 原样转发。前台启动失败（uv 缺失、端口占用等）会打印可读错误并保留窗口。脚本沿用已设置的 `SENTINEL_ORCH_HOME`，便于隔离测试。

## 中控视图（foreground）

`-Action foreground` 启动成功后，终端打印：

- 启动横幅：`AgentSentinel 编排中控已启动`、pid、`host:port`、home、worker mode、启动时间；
- 已注册 agent 表：`role` / `pid` / `status` / `alive`（在线/已停止）/ `last_heartbeat` / `current_task_id`；无注册时显示「暂无已注册 agent」。

默认每 5 秒刷新一次 agent 表；`SENTINEL_ORCH_DASHBOARD_REFRESH_SECONDS=<秒>` 可覆盖，设为 `0` 只打印启动快照。`-Action start`（detached）不输出中控视图，只写日志。启动失败（如端口占用）打印可读错误并以非 0 退出。

可选参数（默认值）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `-Action` | `foreground` | `foreground` / `start` / `stop` / `status` |
| `-Port` | `0` | 0 = 随机空闲端口；`foreground` 与 `start` 生效 |
| `-WorkerMode` | `real` | `real`（`claude -p`）或 `dummy`（测试用） |
| `-BudgetUsd` | `0.50` | 每个真实 worker 的 `--max-budget-usd` |
| `-WorkerTimeoutSeconds` | `600` | worker 进程超时回收 |
| `-WatchdogSeconds` | `30` | 看护线程刷新 heartbeat 的间隔 |
| `-AckTimeoutSeconds` | `120` | ACK 超时（首次超时重投一次） |
| `-LostSeconds` | `120` | 无心跳判定失联 |

运行时数据都在 `.claude/orchestrator/`（gitignored）：`registry.sqlite3`、`token`、`daemon.json`、`logs/`、`run/`。

## 端点与 MCP 工具

HTTP（Bearer token，`/health` 除外）：`/health` `/register` `/resolve` `/status` `/start` `/stop` `/need` `/ack` `/completed` `/heartbeat` `/task` `/tasks` `/step` `/effect` `/shutdown`。

MCP（`.mcp.json` 中的 `sentinel` server）：`orchestrator_register/status/resolve/need/ack/completed/heartbeat/task/tasks`。新会话启动后加载。

`orchestrator_register` 参数为 `role` / `session` / `ref`：MCP server 用自身 `pid`/`pgid` 注册，`command` 记录为 `mcp_server session=<...> ref=<...>`，重复调用幂等更新；daemon 离线时返回 `isError:true` 的结构化错误，不抛异常、不重试、不轮询。新窗口按角色提示词静默调用一次即可。

## 用量、预算与观测

```powershell
# 最近用量与按角色/当日汇总
uv run python -m tools.orchestrator usage
uv run python -m tools.orchestrator usage --role dev --day 2026-11-08

# 观测页（agents / tasks / 最近消息 / 残留 / 预算）
uv run python -m tools.orchestrator observability
uv run python -m tools.orchestrator observability --format json
uv run python -m tools.orchestrator status --verbose
```

预算相关环境变量（脚本已透传）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SENTINEL_ORCH_TASK_MAX_TOKENS` | 200000 | 每任务 billed tokens 上限（80% 警告，100% `budget_exceeded`） |
| `SENTINEL_ORCH_TASK_MAX_MINUTES` | 30 | 每任务分钟上限 |
| `SENTINEL_ORCH_ROLE_BUDGET_TOKENS` | 200000 | 每角色/UTC 日 billed tokens 上限，超出时 `/need` 返回 429 |
| `SENTINEL_ORCH_WORKER_BUDGET_USD` | 0.50 | 传给 `claude -p --max-budget-usd` |

真实 worker 的 usage/cost 在进程退出时从 `claude -p --output-format json` 日志解析；`completed` 对真实 worker 采用延迟回收（worker 自然退出后看护线程记录 usage 并关闭 Job），dummy 仍立即回收。

## 验证“任务结束后没有残留进程”

任务进入 `done` / `failed` / `timeout` 时，daemon 会关闭该 worker 的 Job 并等待 `alive=false`。验证方式：

```powershell
# 1. 任务与 agent 状态
uv run python -m tools.orchestrator status
uv run python -m tools.orchestrator task --task-id <TASK_ID>

# 2. 记录 worker pid 后，确认 pid 已消失
uv run python -c "from tools.orchestrator.launcher import process_alive; print(process_alive(<PID>))"
# 输出 False 即无残留

# 3. 查看回收证据（orphan_detected / alive）
uv run python -m tools.orchestrator stop --role <ROLE>
```

自动化用例见 `tests/orchestrator/test_e2e_flow.py`（dummy 全链路 + 残留断言；真实 worker 用例由 `SENTINEL_ORCH_REAL_SMOKE=1` 显式开启）。
