@echo off
rem Double-click launcher for the AgentSentinel orchestrator daemon (development only).
rem Starts the daemon in the background, waits until /health passes, then exits.
setlocal
cd /d "%~dp0.."
powershell -NoProfile -File "%~dp0start_orchestrator.ps1" -Action start %*
if errorlevel 1 goto failed
echo [AgentSentinel] Orchestrator daemon is running.
exit /b 0

:failed
echo.
echo [AgentSentinel] Failed to start the orchestrator daemon.
echo See .claude\orchestrator\logs\daemon.log for details.
ping -n 11 127.0.0.1 >nul
exit /b 1
