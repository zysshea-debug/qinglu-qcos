@echo off
cd /d "%~dp0"

if not exist "%~dp0venv\Scripts\python.exe" (
    echo VENV_NOT_FOUND
    pause
    exit /b 1
)

"%~dp0venv\Scripts\python.exe" "%~dp0qcos_server.py"
