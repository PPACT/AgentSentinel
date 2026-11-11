@echo off
setlocal

rem Double-click path: relaunch through a hidden VBS launcher so the console
rem closes immediately and no black window stays tied to the app.
rem Use `start_frontend.bat --console` to keep this console for debugging.
if /i "%~1"=="--hidden" goto run
if /i "%~1"=="--console" goto run
if /i "%~1"=="--watch" goto run
start "" /b "%SystemRoot%\System32\wscript.exe" //nologo "%~dp0scripts\start_frontend.vbs" "%~f0"
exit /b 0

:run
title AgentSentinel Console (dev)
set "ROOT=%~dp0..\.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
set "VITE_AGENT_SENTINEL_ROOT=%ROOT%"
set "LOGDIR=%ROOT%\.claude\logs"
set "LAUNCHLOG=%LOGDIR%\frontend-launch.log"
set "CURRENT=%LOGDIR%\frontend-dev.current"
cd /d "%~dp0"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>nul
if /i "%~1"=="--watch" goto watch

rem Diagnostics only: separate file, safe to append even while an old run holds its log.
>>"%LAUNCHLOG%" echo [%DATE% %TIME%] bat start args=%~1 cwd=%CD%
>>"%LAUNCHLOG%" echo PATH=%PATH%
>>"%LAUNCHLOG%" %SystemRoot%\System32\where.exe npm 2>&1
>>"%LAUNCHLOG%" %SystemRoot%\System32\where.exe cargo 2>&1
>>"%LAUNCHLOG%" %SystemRoot%\System32\tasklist.exe /FI "IMAGENAME eq agentsentinel-console.exe" 2>&1
( %SystemRoot%\System32\netstat.exe -ano 2>&1 | %SystemRoot%\System32\findstr.exe /C:":1420 " /C:":8765 " ) >>"%LAUNCHLOG%" 2>&1

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

rem Each run gets a unique log: a viewer/lingering process holding an old log
rem can no longer block startup or make stale content look like a fresh run.
set "LOG=%LOGDIR%\frontend-dev-%RANDOM%%RANDOM%%RANDOM%.log"
set "STATE=%LOG%.state"
> "%CURRENT%" echo %LOG%
%SystemRoot%\System32\cmd.exe /c copy /y nul "%LOG%" >nul 2>nul
if errorlevel 1 (
  >>"%LAUNCHLOG%" echo [%DATE% %TIME%] cannot create run log %LOG%
  exit /b 6
)
>>"%LOG%" echo [%DATE% %TIME%] run start

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

%SystemRoot%\System32\netstat.exe -ano | %SystemRoot%\System32\findstr.exe /C:":8765 " | %SystemRoot%\System32\findstr.exe /C:"LISTENING" >nul
if not errorlevel 1 (
  call :say "note: port 8765 is already in use; the app will reuse it if the daemon is healthy."
)

call :say "starting dev window (tauri dev, hot reload)..."
>>"%LAUNCHLOG%" echo [%DATE% %TIME%] frontend npm dev starting
start "" /b "%SystemRoot%\System32\cmd.exe" /d /c call "%~f0" --watch
if /i "%~1"=="--console" (
  call npm run tauri dev
) else (
  call npm run tauri dev >> "%LOG%" 2>&1
)
set "CODE=%ERRORLEVEL%"
if not defined CODE set "CODE=0"

rem Distinguish "never showed the app window" from "app ran and then exited".
set "RESULT=failed"
%SystemRoot%\System32\findstr.exe /C:"agentsentinel-console.exe" "%LOG%" >nul 2>nul && set "RESULT=exited"
if not defined RESULT set "RESULT=failed"
> "%STATE%" echo %RESULT%:%CODE%
>>"%LAUNCHLOG%" echo [%DATE% %TIME%] bat end code=%CODE% result=%RESULT% log=%LOG%
call :say "tauri dev ended with code %CODE% (%RESULT%)"
exit /b %CODE%

:say
echo [AgentSentinel] %~1
>>"%LOG%" echo [%DATE% %TIME%] %~1
exit /b 0

:watch
rem 启动计时：只记录“app 进程出现”这一条，与 npm start 行配对，
rem 用于区分前端编译慢还是 daemon/运行时卡住。最多等 120 秒。
for /l %%I in (1,1,120) do (
  %SystemRoot%\System32\tasklist.exe /FI "IMAGENAME eq agentsentinel-console.exe" 2>nul | %SystemRoot%\System32\find.exe /I "agentsentinel-console.exe" >nul
  if not errorlevel 1 (
    >>"%LAUNCHLOG%" echo [%DATE% %TIME%] frontend app process up
    exit /b 0
  )
  %SystemRoot%\System32\ping.exe -n 2 127.0.0.1 >nul
)
>>"%LAUNCHLOG%" echo [%DATE% %TIME%] frontend app process not seen within 120s
exit /b 1
