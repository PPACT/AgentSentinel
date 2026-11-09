@echo off
rem One-click launcher for AgentSentinel Console (release build).
rem Build once with: npm run tauri build -- --no-bundle
set "EXE=%~dp0..\..\.claude\tauri-target\release\agentsentinel-console.exe"
if not exist "%EXE%" (
  echo AgentSentinel Console release exe not found:
  echo   %EXE%
  echo.
  echo Build it first:
  echo   npm run tauri build -- --no-bundle
  pause
  exit /b 1
)
start "" "%EXE%"
exit /b 0
