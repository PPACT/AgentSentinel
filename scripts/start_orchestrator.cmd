@echo off
rem Orchestrator launcher (development only).
rem No arguments: run the foreground dashboard in event-driven watch mode; the window stays open after exit.
rem Any arguments: forward them to start_orchestrator.ps1 as-is.
setlocal
rem Force UTF-8 for the console and every spawned Python process (daemon/workers).
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not "%~1"=="" goto forward

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_orchestrator.ps1" -Action foreground -Watch
set "CODE=%ERRORLEVEL%"
echo.
pause
exit /b %CODE%

:forward
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_orchestrator.ps1" %*
exit /b %ERRORLEVEL%
