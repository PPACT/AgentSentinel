@echo off
setlocal
title AgentSentinel Console (dev)
set "ROOT=%~dp0..\.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
set "VITE_AGENT_SENTINEL_ROOT=%ROOT%"
cd /d "%~dp0"

%SystemRoot%\System32\tasklist.exe /FI "IMAGENAME eq agentsentinel-console.exe" 2>nul | %SystemRoot%\System32\find.exe /I "agentsentinel-console.exe" >nul
if not errorlevel 1 (
  echo [AgentSentinel] frontend is already running. Close the old window first.
  pause
  exit /b 1
)

%SystemRoot%\System32\netstat.exe -ano | %SystemRoot%\System32\findstr.exe ":1420" | %SystemRoot%\System32\findstr.exe "LISTENING" >nul
if not errorlevel 1 (
  echo [AgentSentinel] port 1420 is in use - stale Vite? Close the old dev window or end node.exe, then retry.
  pause
  exit /b 1
)

echo [AgentSentinel] starting dev window (tauri dev, hot reload)...
echo [AgentSentinel] close this window to stop the app.
echo.
call npm run tauri dev
set "CODE=%ERRORLEVEL%"
echo.
echo [AgentSentinel] tauri dev exited with code %CODE%
pause
exit /b %CODE%
