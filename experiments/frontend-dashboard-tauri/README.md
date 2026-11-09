# AgentSentinel Console（C1 Tauri 试验）

**隔离试验，不并入产品、不 push。** Tauri v2 + React + Vite + TypeScript + Tailwind v4 + shadcn/ui；
Rust 端只做插件初始化与 `Command` 拉起 Python daemon，网络/SSE/状态全部在 JS/TS。

## 技术栈与边界

- 前端：React 19、Vite 8、TypeScript 5.9、Tailwind v4、shadcn/ui（radix-nova 预设）、Inter / JetBrains Mono。
- Rust：Tauri v2 + `plugin-shell` / `plugin-http` / `plugin-fs`；无自定义业务命令。
- 数据：`plugin-http` 的 `fetch` + `ReadableStream` 读 `GET /events`；不使用 `EventSource`、无 `setInterval`、无循环轮询。
- token：由 `plugin-fs` 从 `<项目根>/.claude/orchestrator/token` 读入 JS 内存（`scripts/tauri.mjs` 生成本机读取授权），代理/打包产物中不出现 token。
- 视口：顶栏 + 可拖拽 Agent/Task 双栏 + 底部可折叠 Console（客户端事件流、虚拟滚动，上限 500 条）；`Ctrl+K` 命令面板；日志虚拟滚动。
- 非目标：完整 C1 功能、日志流/stdin、打包分发、并入产品。

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `VITE_AGENT_SENTINEL_ROOT` | 否 | AgentSentinel 项目根（daemon 的 `cwd`）。`npm run tauri ...` 会由 `scripts/tauri.mjs` 自动设为项目根；只有直接跑 `vite`/`cargo` 时才需要手动设置。 |

daemon 使用**项目默认 home** `<项目根>/.claude/orchestrator`：应用读取其中的 `daemon.json` 与 `token`，
连接 dev/doc/MCP 注册的同一个 daemon。`scripts/tauri.mjs` 会生成本机的 fs 读取授权（`local.dev.json`，gitignored）。

## 运行

```powershell
cd experiments/frontend-dashboard-tauri
npm install
npm run tauri dev
```

应用启动时：读取 `<项目根>/.claude/orchestrator/daemon.json` 连接现有 daemon；若不可用，则用该 home
执行 `uv run python -m tools.orchestrator.service --serve --port 8765` 拉起默认 daemon。失败会在顶栏下方
显示可读错误。**关闭窗口不保证回收 daemon**（优雅退出属阶段 C3）；开发时用 `POST /shutdown` 收掉
（Bearer token 在 `<项目根>/.claude/orchestrator/token`）。

> **构建目录**：`npm run tauri ...` 通过 `scripts/tauri.mjs` 把 `CARGO_TARGET_DIR` 固定为
> `<项目根>/.claude/tauri-target`。Windows 下沙箱/工具可能给项目树打上 Low 完整性标签，
> 产物若继承该标签会无法写 `%LOCALAPPDATA%`，Tauri/WebView2 启动时报 `os error 5`。
> 直接用 `cargo build` 时请自行设置同一环境变量。

## 数据来源（dev/doc agent）

- 前端连接的是**项目默认 daemon**（`.claude/orchestrator`），与 dev/doc 会话、MCP、`start_orchestrator.cmd`
  使用的是同一个 registry，因此能直接看到已注册的 dev/doc agent。
- 若默认 daemon 已在运行（端口任意），应用按 `daemon.json` 连接，不会重复拉起。
- 若默认 daemon 未运行，应用用同一 home 在 `8765` 拉起；registry 中已有的 dev/doc 记录会随之显示
  （`alive` 由 pid 是否存活决定）。
- 页面显示「暂无 agent」只表示该 registry 目前确实没有记录；需要时可手动注册 demo：

  ```powershell
  # 在项目根执行
  $orch  = Join-Path (Get-Location) ".claude\orchestrator"
  $token = (Get-Content (Join-Path $orch "token") -Raw).Trim()
  $port  = (Get-Content (Join-Path $orch "daemon.json") -Raw | ConvertFrom-Json).port
  $body  = @{ role = "demo"; pid = $PID; status = "registered" } | ConvertTo-Json
  Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/register" `
    -Headers @{ Authorization = "Bearer $token" } -ContentType "application/json" -Body $body
  ```

  停止它：

  ```powershell
  Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/stop" `
    -Headers @{ Authorization = "Bearer $token" } -ContentType "application/json" `
    -Body '{"role":"demo"}'
  ```

## 开发期一键启动（双击）

**双击 `start_frontend.bat`** 即可打开开发窗口（等价 `npm run tauri dev`，带热更新）。
窗口保留控制台用于显示 Vite/Tauri 日志；关闭控制台或窗口即停止开发进程。

脚本会：
- 将 `~/.cargo/bin` 加入 PATH、设置 `VITE_AGENT_SENTINEL_ROOT`；
- 通过 `scripts/tauri.mjs` 自动设置 `CARGO_TARGET_DIR=<项目根>/.claude/tauri-target`；
- 直接执行 `npm run tauri dev`，修改前端代码后热更新。

> 开发期（C1/C2）**不要**执行 `npm run tauri build`；release exe 与安装包留到阶段 C3。

## Console 日志流（C2）

Console 现在订阅后端真实日志：

- `GET /logs/stream?tail=100[&role=<role>]`（SSE，Bearer）：先回放每个 `logs/*.log` 的最后 `tail` 行，
  之后持续推送新增行；帧格式 `id` + `event: log` + `data: {"role","line","ts"}`；新出现的角色日志
  在约 2s 内自动纳入；每 20s 发 `: ping`。
- 前端把日志行以 `[role] line` 前缀并入 Console（与连接/停止等客户端事件同一列表，虚拟滚动、上限 500 条）。
- 当前覆盖 **orchestrator daemon 与 worker 日志**（`.claude/orchestrator/logs/*.log`）。dev/doc 等外部
  Claude Code 窗口的终端输出不在 daemon 可见范围内；如需接入，需要会话侧主动把日志写入 daemon。

## 校验

```bash
npm run build      # tsc + vite build
npm run lint       # eslint
npm test           # vitest（SSE 解析/重连/格式化）
npx tauri info     # 工具链概览
cd src-tauri && cargo check
```

静态要求（人工/CI 检查）：源码无 `setInterval` / `new EventSource` / 循环 fetch；`dist/` 无 token。

## 已知限制

- 脚手架阶段：Agent/Task 数据来自 `/status` + `/tasks`；Console 展示**客户端事件**（连接/SSE/停止操作，虚拟滚动），后端日志流接口未实现（C2）。
- daemon 地址优先读 `.claude/orchestrator/daemon.json`（运行中则复用）；仅在拉起新 daemon 时使用固定端口 `8765`；capability URL scope 为 `127.0.0.1:*`。
- capability 改动后需重启 `tauri dev` 才生效。
