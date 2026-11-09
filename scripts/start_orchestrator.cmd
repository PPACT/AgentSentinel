@echo off
rem Orchestrator launcher (development only).
rem No arguments: run the foreground dashboard in event-driven watch mode; the window stays open after exit.
rem Any arguments: forward them to start_orchestrator.ps1 as-is.
setlocal
if not "%~1"=="" goto forward

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_orchestrator.ps1" -Action foreground -Watch
set "CODE=%ERRORLEVEL%"
echo.
pause
exit /b %CODE%

:forward
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_orchestrator.ps1" %*
exit /b %ERRORLEVEL%
