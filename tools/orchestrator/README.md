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
scripts\start_orchestrator.cmd -Action foreground
scripts\start_orchestrator.cmd -Action status
scripts\start_orchestrator.cmd -Action stop
```

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

MCP（`.mcp.json` 中的 `sentinel` server）：`orchestrator_status/resolve/need/ack/completed/heartbeat/task/tasks`。新会话启动后加载。

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
