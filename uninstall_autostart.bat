@echo off
rem ============================================================
rem  Remove QCOS auto-start task (use to disable/rollback)
rem ============================================================
set TASK_NAME=QCOS_Server
schtasks /Delete /TN "%TASK_NAME%" /F
echo Auto-start task removed.
pause
