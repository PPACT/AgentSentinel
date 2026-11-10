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
set "LOGDIR=%ROOT%\.claude\logs"
set "LOG=%LOGDIR%\frontend-dev.log"
set "STATE=%LOG%.state"
cd /d "%~dp0"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>nul

%SystemRoot%\System32\tasklist.exe /FI "IMAGENAME eq agentsentinel-console.exe" 2>nul | %SystemRoot%\System32\find.exe /I "agentsentinel-console.exe" >nul
if not errorlevel 1 (
  rem Log is held open by the running dev process; do not touch it here.
  echo [AgentSentinel] frontend is already running. Close the old window first.
  exit /b 2
)

%SystemRoot%\System32\netstat.exe -ano | %SystemRoot%\System32\findstr.exe /C:":1420 " | %SystemRoot%\System32\findstr.exe /C:"LISTENING" >nul
if not errorlevel 1 (
  echo [AgentSentinel] port 1420 is in use - stale Vite? Close the old dev window or end node.exe, then retry.
  exit /b 3
)

%SystemRoot%\System32\where.exe npm >nul 2>nul
if errorlevel 1 (
  call :say "npm not found in PATH. Install Node.js or open a terminal with npm available."
  exit /b 4
)

%SystemRoot%\System32\where.exe cargo >nul 2>nul
if errorlevel 1 (
  call :say "cargo not found. Install Rust (rustup) or add %USERPROFILE%\.cargo\bin to PATH."
  exit /b 5
)

rem Keep one previous run, then start a fresh log so state detection is unambiguous.
if exist "%LOG%" move /y "%LOG%" "%LOG%.1" >nul 2>nul
if exist "%STATE%" del /q "%STATE%" >nul 2>nul

%SystemRoot%\System32\netstat.exe -ano | %SystemRoot%\System32\findstr.exe /C:":8765 " | %SystemRoot%\System32\findstr.exe /C:"LISTENING" >nul
if not errorlevel 1 (
  call :say "note: port 8765 is already in use; the app will reuse it if the daemon is healthy."
)

call :say "starting dev window (tauri dev, hot reload)..."
if /i "%~1"=="--console" (
  call npm run tauri dev
) else (
  call npm run tauri dev >> "%LOG%" 2>&1
)
set "CODE=%ERRORLEVEL%"

rem Distinguish "never showed the app window" from "app ran and then exited".
set "RESULT=failed"
%SystemRoot%\System32\findstr.exe /C:"agentsentinel-console.exe" "%LOG%" >nul 2>nul && set "RESULT=exited"
> "%STATE%" echo %RESULT%:%CODE%
call :say "tauri dev ended with code %CODE% (%RESULT%)"
exit /b %CODE%

:say
echo [AgentSentinel] %~1
>>"%LOG%" echo [%DATE% %TIME%] %~1
exit /b 0
