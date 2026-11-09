# AgentSentinel Console（C1 Tauri 试验）

**隔离试验，不并入产品、不 push。** Tauri v2 + React + Vite + TypeScript + Tailwind v4 + shadcn/ui；
Rust 端只做插件初始化与 `Command` 拉起 Python daemon，网络/SSE/状态全部在 JS/TS。

## 技术栈与边界

- 前端：React 19、Vite 8、TypeScript 5.9、Tailwind v4、shadcn/ui（radix-nova 预设）、Inter / JetBrains Mono。
- Rust：Tauri v2 + `plugin-shell` / `plugin-http` / `plugin-fs`；无自定义业务命令。
- 数据：`plugin-http` 的 `fetch` + `ReadableStream` 读 `GET /events`；不使用 `EventSource`、无 `setInterval`、无循环轮询。
- token：由 `plugin-fs` 从 app data 下的 `orchestrator/token` 读入 JS 内存，代理/打包产物中不出现 token。
- 视口：顶栏 + 可拖拽 Agent/Task 双栏 + 底部可折叠 Console 占位；`Ctrl+K` 命令面板占位；日志虚拟滚动占位。
- 非目标：完整 C1 功能、日志流/stdin、打包分发、并入产品。

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `VITE_AGENT_SENTINEL_ROOT` | 否 | AgentSentinel 项目根（daemon 的 `cwd`）。`npm run tauri ...` 会由 `scripts/tauri.mjs` 自动设为项目根；只有直接跑 `vite`/`cargo` 时才需要手动设置。 |

daemon 的 `SENTINEL_ORCH_HOME` 由应用固定为 **`appDataDir()/orchestrator`**（Windows 下即
`%APPDATA%\com.agentsentinel.console\orchestrator`），与 capability 的 `$APPDATA/orchestrator/*` 一致。

## 运行

```powershell
cd experiments/frontend-dashboard-tauri
npm install
npm run tauri dev
```

应用启动时：若 `http://127.0.0.1:8765/health` 不可用，则用 `uv run python -m tools.orchestrator.service
--serve --port 8765` 拉起隔离 daemon；失败会在顶栏下方显示可读错误。**关闭窗口不保证回收 daemon**
（优雅退出属阶段 C3）；开发时用 `POST /shutdown` 收掉（Bearer token 在
`%APPDATA%\com.agentsentinel.console\orchestrator\token`）。

> **构建目录**：`npm run tauri ...` 通过 `scripts/tauri.mjs` 把 `CARGO_TARGET_DIR` 固定为
> `<项目根>/.claude/tauri-target`。Windows 下沙箱/工具可能给项目树打上 Low 完整性标签，
> 产物若继承该标签会无法写 `%LOCALAPPDATA%`，Tauri/WebView2 启动时报 `os error 5`。
> 直接用 `cargo build` 时请自行设置同一环境变量。

## 隔离数据与「空表」说明

- 应用拉起的 daemon 使用**独立 home**：`appDataDir()/orchestrator`（Windows 下为
  `%APPDATA%\com.agentsentinel.console\orchestrator`），与项目的 `.claude/orchestrator` 隔离。
- 首次启动 registry 为空，页面显示「暂无 agent / 暂无任务」是**正常现象**。
- 若 `127.0.0.1:8765` 已被其他 daemon 占用，应用会复用该 daemon（不再重复拉起），此时数据来自其 home。
- 注册一个 demo agent（PowerShell，daemon 已运行）：

  ```powershell
  $home  = Join-Path $env:APPDATA "com.agentsentinel.console\orchestrator"
  $token = (Get-Content (Join-Path $home "token") -Raw).Trim()
  $body  = @{ role = "demo"; pid = $PID; status = "registered" } | ConvertTo-Json
  Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/register" `
    -Headers @{ Authorization = "Bearer $token" } -ContentType "application/json" -Body $body
  ```

  页面应在 SSE 事件驱动下自动出现 `demo`；停止它：

  ```powershell
  Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/stop" `
    -Headers @{ Authorization = "Bearer $token" } -ContentType "application/json" `
    -Body '{"role":"demo"}'
  ```

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

- 脚手架阶段：Agent/Task 数据来自 `/status` + `/tasks`，Console 为占位；日志流接口未实现。
- 固定端口 `8765`；后续接入正式 daemon 时改读 `daemon.json` 并同步 capability URL scope。
- capability 改动后需重启 `tauri dev` 才生效。
