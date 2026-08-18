@echo off
rem ============================================================
rem  QCOS server launcher with auto-restart (watchdog)
rem  - Restarts the server if it crashes/exits
rem  - To stop gracefully: create an empty file "qcos.stop"
rem    in this folder, the watcher will exit on next loop
rem  NOTE: edit PYTHON path below when deploying to a new PC
rem ============================================================
setlocal
cd /d "%~dp0"
set PYTHON=C:\Users\zyssh\.workbuddy\binaries\python\envs\default\Scripts\python.exe
set LOG=%~dp0qcos_run.log

:loop
if exist "%~dp0qcos.stop" (
    echo [%date% %time%] Stop requested, exiting watcher. >> "%LOG%"
    del "%~dp0qcos.stop" >nul 2>&1
    goto end
)
echo [%date% %time%] Starting QCOS server... >> "%LOG%"
"%PYTHON%" app.py >> "%LOG%" 2>&1
echo [%date% %time%] QCOS exited (code %errorlevel%). Restarting in 3s... >> "%LOG%"
timeout /t 3 /nobreak >nul
goto loop

:end
echo [%date% %time%] QCOS watcher stopped. >> "%LOG%"
