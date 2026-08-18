@echo off
rem ============================================================
rem  Register QCOS to auto-start when this user logs in
rem  (uses Windows Task Scheduler, no 3rd-party tools)
rem  Run this file once. Right-click -> Run as administrator
rem  if the task creation fails.
rem ============================================================
set QCOS_DIR=%~dp0
set TASK_NAME=QCOS_Server

schtasks /Create /TN "%TASK_NAME%" /TR "\"%QCOS_DIR%run_qcos.bat\"" /SC ONLOGON /RL HIGHEST /F
if %errorlevel%==0 (
    echo [OK] Auto-start task "%TASK_NAME%" created.
    echo Starting it now...
    schtasks /Run /TN "%TASK_NAME%"
    echo Done. QCOS will launch automatically on next login.
) else (
    echo [FAIL] Could not create task. Try right-click -> Run as administrator.
)
pause
