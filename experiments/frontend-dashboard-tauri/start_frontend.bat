@echo off
setlocal
title AgentSentinel Console (dev)
set "ROOT=%~dp0..\.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
set "VITE_AGENT_SENTINEL_ROOT=%ROOT%"
cd /d "%~dp0"
echo [AgentSentinel] starting dev window (tauri dev, hot reload)...
echo [AgentSentinel] close this window to stop the app.
echo.
call npm run tauri dev
set "CODE=%ERRORLEVEL%"
echo.
echo [AgentSentinel] tauri dev exited with code %CODE%
pause
exit /b %CODE%
