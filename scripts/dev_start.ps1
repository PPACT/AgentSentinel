# Dev one-click start: launch the doc and dev Claude Code windows (or one role).
# The cold-start script writes .claude/mailbox/registry.json through
# `scripts/wake.py register`; agents never run register or send messages.
#
# Usage:
#   .\dev_start.ps1
#   .\dev_start.ps1 -Role dev
#   .\dev_start.ps1 -DocRole doc -DevRole dev
#   .\dev_start.ps1 -Prompt "custom first message"
#
# If execution policy blocks the script:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\dev_start.ps1

param(
    [string]$Role = '',
    [string]$DocRole = 'doc',
    [string]$DevRole = 'dev',
    [string]$Prompt = 'Read STATUS.md and docs/local/交流区/交流区.md, then wait for handoff.'
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

$roles = if ($Role) { @($Role) } else { @($DocRole, $DevRole) }

foreach ($roleName in $roles) {
    if ($roleName -notmatch '^[a-z0-9_-]+$') {
        throw "Invalid role '$roleName': use lowercase letters, digits, '-' or '_'."
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

$hasUv = $null -ne (Get-Command uv -ErrorAction SilentlyContinue)
if (-not $hasUv) {
    Write-Warning "uv not found on PATH; registry will not be written by this script."
}

$configDir = if ($env:CLAUDE_CONFIG_DIR) {
    $env:CLAUDE_CONFIG_DIR
}
else {
    Join-Path $env:USERPROFILE '.claude'
}

function ConvertTo-QuotedArgument {
    # Windows command-line quoting: double the backslashes before a quote,
    # escape the quote itself, and double trailing backslashes.
    param([Parameter(Mandatory)][string]$Value)

    $escaped = $Value -replace '([\\]*)"', '$1$1\"'
    $escaped = $escaped -replace '([\\]+)$', '$1$1'
    return '"' + $escaped + '"'
}

function Invoke-WakeRegister {
    param(
        [Parameter(Mandatory)][string]$RoleName,
        [string]$SessionName = '',
        [string]$Ref = '',
        [Parameter(Mandatory)][int]$ProcessId
    )

    if (-not $hasUv) {
        return
    }
    $wakeScript = Join-Path $Root 'scripts\wake.py'
    & uv run --project $Root python $wakeScript register `
        --role $RoleName --session $SessionName --ref $Ref --pid $ProcessId | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "wake.py register failed for role $RoleName (exit $LASTEXITCODE)."
    }
}

function Wait-SessionRecord {
    param(
        [Parameter(Mandatory)][int]$ProcessId,
        [Parameter(Mandatory)][string]$ConfigDir
    )

    $sessions = Join-Path $ConfigDir 'sessions'
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        $path = Join-Path $sessions ("{0}.json" -f $ProcessId)
        if (Test-Path -LiteralPath $path) {
            try {
                return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
            }
            catch {
                # The registry record may still be mid-write; retry.
            }
        }
        Start-Sleep -Milliseconds 250
    }
    return $null
}

function Start-ClaudeWindow {
    param(
        [Parameter(Mandatory)][string]$RoleName,
        [string]$InitialPrompt
    )

    # The child inherits the environment variable at spawn time; the new
    # PostToolUse hook can also fall back to it when mapping session -> role.
    $previousRole = $env:SENTINEL_ROLE
    $env:SENTINEL_ROLE = $RoleName
    try {
        # Pass the prompt as one quoted argument; Start-Process joins array
        # elements with spaces without adding quotes in Windows PowerShell 5.1.
        $argline = ConvertTo-QuotedArgument -Value $InitialPrompt
        $process = Start-Process -FilePath $claude -WorkingDirectory $Root `
            -ArgumentList $argline -PassThru
    }
    finally {
        if ($null -eq $previousRole) {
            Remove-Item Env:SENTINEL_ROLE -ErrorAction SilentlyContinue
        }
        else {
            $env:SENTINEL_ROLE = $previousRole
        }
    }

    $record = Wait-SessionRecord -ProcessId $process.Id -ConfigDir $configDir
    $sessionName = ''
    $ref = ''
    if ($null -ne $record) {
        if ($null -ne $record.name) {
            $sessionName = [string]$record.name
        }
        if ($null -ne $record.sessionId) {
            $ref = [string]$record.sessionId
        }
    }

    Invoke-WakeRegister -RoleName $RoleName -SessionName $sessionName -Ref $ref `
        -ProcessId $process.Id

    # Machine-readable line consumed by `wake.py notify` when it degrades.
    $info = @{
        role    = $RoleName
        session = $sessionName
        ref     = $ref
        pid     = $process.Id
    } | ConvertTo-Json -Compress
    Write-Output "SENTINEL_REGISTER:$info"
}

Write-Host "Project root: $Root"
Write-Host ("Launching window(s): " + ($roles -join ' / '))

foreach ($roleName in $roles) {
    Start-ClaudeWindow -RoleName $roleName -InitialPrompt $Prompt
    Start-Sleep -Milliseconds 500
}

Write-Host "Window(s) launched. Registry written by the cold-start script."
