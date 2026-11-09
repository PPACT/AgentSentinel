# C1 浏览器版中控试验

隔离试验，不接入产品。复用项目既有 Python 依赖（FastAPI / uvicorn / httpx），**零新增依赖**，
不使用 Vite / React / Node 构建链，前端为原生 HTML/CSS/ES Module。

## 方案要点

- `server.py`：FastAPI 本地开发代理。
  - 静态托管 `static/`；
  - `/api/*` 反向代理到 daemon（默认读取 `$SENTINEL_ORCH_HOME/daemon.json`）；
  - 从 `$SENTINEL_ORCH_HOME/token` 读取 Bearer token 并注入请求，**token 不进入浏览器**；
  - `/api/events` 流式透传 SSE（snapshot / heartbeat / shutdown / `: ping`）。
- 前端：`fetch()` + `ReadableStream` 读 SSE（不使用 `EventSource`，因为不能带 `Authorization`）；
  收到任意事件后去抖 250ms 重取 `/tasks`，**无 `setInterval`、无循环轮询**。
- 接口契约见 `docs/local/frontend-dashboard-plan.md` §5.2；字段以 `tools/orchestrator/registry.py`、
  `tools/orchestrator/router.py` 为准。

## 启动

1. 启动 daemon（二选一）：

   ```bat
   scripts\start_orchestrator.cmd -Action start
   ```

   或前台中控（另一个终端）：`scripts\start_orchestrator.cmd`。

2. 启动试验代理（项目根目录）：

   ```bash
   uv run python experiments/frontend-dashboard/server.py --port 8787
   ```

3. 浏览器打开 <http://127.0.0.1:8787>。

`server.py` 参数 / 环境变量：

| 项 | 默认 | 说明 |
|---|---|---|
| `--host` | `127.0.0.1` | 仅监听本机 |
| `--port` | `8787` | 代理端口 |
| `SENTINEL_ORCH_HOME` | `.claude/orchestrator` | daemon home（读取 `token` / `daemon.json`） |
| `SENTINEL_ORCH_URL` | 空 | 显式指定 daemon 地址；设置后优先于 `daemon.json` |

## 测试

```bash
# 代理契约：静态托管、Bearer 注入、SSE 透传、401、daemon 未启动 503
uv run pytest experiments/frontend-dashboard/tests

# 前端逻辑（Node 24 内置 test runner，无 npm 依赖）
node --test experiments/frontend-dashboard/web-tests/*.test.mjs
```

静态检查（无轮询 / 无 token 泄漏）包含在 `test_static_index_served_without_token_leak` 中。

## 已知限制

- 只做 MVP：Agent 列表、任务列表、daemon 状态、停止角色；无日志流 / stdin / 历史图表。
- 无前端构建与 TypeScript：采用原生 ES Module；如后续迁入产品再评估技术栈。
- 浏览器端不做鉴权输入：token 由本地代理注入；不要把代理暴露到非本机地址。
- daemon 停止时页面显示「中控未启动」，SSE 按指数退避重连；daemon 重启后自动恢复。
- 试验产物（`node_modules/`、`dist/`）已加入 `.gitignore`，本目录不 push。
