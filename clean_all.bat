@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title AgentSentinel clean_all
echo [清理中] 正在扫描残留进程...
"%~dp0.venv\Scripts\python.exe" -u "%~dp0scripts\clean_all.py"
echo.
echo 按任意键退出...
pause >nul
