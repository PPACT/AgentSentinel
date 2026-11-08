@echo off
rem Orchestrator launcher (development only).
rem No arguments: start the daemon in the background and wait until /health passes.
rem Any arguments: forward them to start_orchestrator.ps1 as-is.
setlocal
if "%~1"=="" goto start

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_orchestrator.ps1" %*
exit /b %ERRORLEVEL%

:start
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_orchestrator.ps1" -Action start
if errorlevel 1 goto failed
echo [AgentSentinel] Orchestrator daemon is running.
exit /b 0

:failed
echo.
echo [AgentSentinel] Failed to start the orchestrator daemon.
echo See .claude\orchestrator\logs\daemon.log for details.
ping -n 11 127.0.0.1 >nul
exit /b 1
