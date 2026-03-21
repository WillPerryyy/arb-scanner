@echo off
title Arb Scanner — Service Launcher
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo  Starting Arb Scanner services...
echo.

backend\.venv\Scripts\python.exe start.py

if errorlevel 1 (
    echo.
    echo  [ERROR] Launcher exited with an error. See output above.
    echo.
)

pause
