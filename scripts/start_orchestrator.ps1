<#
.SYNOPSIS
  AgentSentinel 编排服务 daemon 的启动/停止脚本（开发期工具，不接入产品）。

.DESCRIPTION
  默认在前台常驻运行 daemon；Ctrl+C 会触发优雅退出并回收全部 worker。
  也可以使用 -Action start 让 CLI 以 detached 方式启动（注意：agent 的
  Bash 进程树可能回收 detached daemon，长期使用建议 foreground）。

.EXAMPLE
  .\scripts\start_orchestrator.ps1 -Action foreground

.EXAMPLE
  .\scripts\start_orchestrator.ps1 -Action status

.EXAMPLE
  .\scripts\start_orchestrator.ps1 -Action stop

.EXAMPLE
  .\scripts\start_orchestrator.ps1 -Action start -Port 8765 -WorkerMode dummy
#>
param(
    [ValidateSet('foreground', 'start', 'stop', 'status')]
    [string]$Action = 'foreground',

    [int]$Port = 0,

    [ValidateSet('real', 'dummy')]
    [string]$WorkerMode = 'real',

    [double]$BudgetUsd = 0.50,
    [double]$WorkerTimeoutSeconds = 600,
    [double]$WatchdogSeconds = 30,
    [double]$AckTimeoutSeconds = 120,
    [double]$LostSeconds = 120
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv not found in PATH. Run this script from a shell where the project's uv is available."
}

$env:SENTINEL_ORCH_HOME = Join-Path $Root '.claude\orchestrator'
$env:SENTINEL_ORCH_WORKER_MODE = $WorkerMode
$env:SENTINEL_ORCH_WORKER_BUDGET_USD = "$BudgetUsd"
$env:SENTINEL_ORCH_WORKER_TIMEOUT_SECONDS = "$WorkerTimeoutSeconds"
$env:SENTINEL_ORCH_WATCHDOG_SECONDS = "$WatchdogSeconds"
$env:SENTINEL_ORCH_ACK_TIMEOUT_SECONDS = "$AckTimeoutSeconds"
$env:SENTINEL_ORCH_LOST_SECONDS = "$LostSeconds"
$env:PYTHONUTF8 = '1'

switch ($Action) {
    'foreground' {
        Write-Host "Starting orchestrator daemon in foreground (Ctrl+C to stop)..."
        Write-Host "  home:        $env:SENTINEL_ORCH_HOME"
        Write-Host "  worker mode: $WorkerMode"
        & uv run python -m tools.orchestrator.service --serve --port $Port
    }
    'start' {
        & uv run python -m tools.orchestrator daemon start --port $Port
    }
    'stop' {
        & uv run python -m tools.orchestrator daemon stop
    }
    'status' {
        & uv run python -m tools.orchestrator daemon status
    }
}
