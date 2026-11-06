# Dev one-click start: launch the doc and dev Claude Code windows together.
# Role is injected as a process-level SENTINEL_ROLE; the SessionStart hook then
# injects the register instruction, so each window registers itself on turn one.
#
# Usage:
#   .\dev_start.ps1
#   .\dev_start.ps1 -DocRole doc -DevRole dev
#   .\dev_start.ps1 -Prompt "custom first message"
#
# If execution policy blocks the script:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\dev_start.ps1

param(
    [string]$DocRole = 'doc',
    [string]$DevRole = 'dev',
    [string]$Prompt = 'Register this window with scripts/wake.py (follow the SessionStart instruction), then read STATUS.md and wait for handoff.'
)

$ErrorActionPreference = 'Stop'

# Accept the script in either the repository root or scripts/.
$Root = $PSScriptRoot
if ((Split-Path -Leaf $Root) -eq 'scripts') {
    $Root = Split-Path -Parent $Root
}

if (-not (Test-Path (Join-Path $Root 'scripts\wake.py'))) {
    throw "scripts/wake.py not found. Put this script in the repository root or scripts\."
}

foreach ($role in @($DocRole, $DevRole)) {
    if ($role -notmatch '^[a-z0-9_-]+$') {
        throw "Invalid role '$role': use lowercase letters, digits, '-' or '_' (wake.py role format)."
    }
}

$command = Get-Command claude -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $command) {
    # Fall back to any resolvable command (e.g. a shell shim on PATH).
    $command = Get-Command claude -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $command) {
    throw "claude CLI not found on PATH."
}
$claude = $command.Source
if (-not $claude) {
    $claude = 'claude'
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Warning "uv not found on PATH; the SessionStart hook may fail to run."
}

function ConvertTo-QuotedArgument {
    # Windows command-line quoting: double the backslashes before a quote,
    # escape the quote itself, and double trailing backslashes.
    param([Parameter(Mandatory)][string]$Value)

    $escaped = $Value -replace '([\\]*)"', '$1$1\"'
    $escaped = $escaped -replace '([\\]+)$', '$1$1'
    return '"' + $escaped + '"'
}

function Start-ClaudeWindow {
    param(
        [string]$Role,
        [string]$InitialPrompt
    )

    # The child inherits the environment variable at spawn time.
    $previousRole = $env:SENTINEL_ROLE
    $env:SENTINEL_ROLE = $Role
    try {
        # Pass the prompt as one quoted argument; Start-Process joins array
        # elements with spaces without adding quotes in Windows PowerShell 5.1.
        $argline = ConvertTo-QuotedArgument -Value $InitialPrompt
        Start-Process -FilePath $claude -WorkingDirectory $Root -ArgumentList $argline | Out-Null
    }
    finally {
        if ($null -eq $previousRole) {
            Remove-Item Env:SENTINEL_ROLE -ErrorAction SilentlyContinue
        }
        else {
            $env:SENTINEL_ROLE = $previousRole
        }
    }
}

Write-Host "Project root: $Root"
Write-Host "Launching windows: $DocRole / $DevRole"

Start-ClaudeWindow -Role $DocRole -InitialPrompt $Prompt
Start-Sleep -Milliseconds 500
Start-ClaudeWindow -Role $DevRole -InitialPrompt $Prompt

Write-Host "Two windows launched. Each agent registers itself from the SessionStart hint."
