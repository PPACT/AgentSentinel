@echo off
setlocal

rem Double-click path: relaunch through a hidden VBS launcher so the console
rem closes immediately and no black window stays tied to the app.
rem Use `start_frontend.bat --console` to keep this console for debugging.
if /i "%~1"=="--hidden" goto run
if /i "%~1"=="--console" goto run
start "" /b "%SystemRoot%\System32\wscript.exe" //nologo "%~dp0scripts\start_frontend.vbs" "%~f0"
exit /b 0

:run
title AgentSentinel Console (dev)
set "ROOT=%~dp0..\.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
set "VITE_AGENT_SENTINEL_ROOT=%ROOT%"
set "LOG=%ROOT%\.claude\logs\frontend-dev.log"
cd /d "%~dp0"
if not exist "%ROOT%\.claude\logs" mkdir "%ROOT%\.claude\logs" >nul 2>nul

%SystemRoot%\System32\tasklist.exe /FI "IMAGENAME eq agentsentinel-console.exe" 2>nul | %SystemRoot%\System32\find.exe /I "agentsentinel-console.exe" >nul
if not errorlevel 1 (
  call :say "frontend is already running. Close the old window first."
  exit /b 2
)

%SystemRoot%\System32\netstat.exe -ano | %SystemRoot%\System32\findstr.exe /C:":1420 " | %SystemRoot%\System32\findstr.exe /C:"LISTENING" >nul
if not errorlevel 1 (
  call :say "port 1420 is in use - stale Vite? Close the old dev window or end node.exe, then retry."
  exit /b 3
)

call :say "starting dev window (tauri dev, hot reload)..."
if /i "%~1"=="--console" (
  call npm run tauri dev
) else (
  call npm run tauri dev >> "%LOG%" 2>&1
)
set "CODE=%ERRORLEVEL%"
call :say "tauri dev exited with code %CODE%"
exit /b %CODE%

:say
echo [AgentSentinel] %~1
>>"%LOG%" echo [%DATE% %TIME%] %~1
exit /b 0
