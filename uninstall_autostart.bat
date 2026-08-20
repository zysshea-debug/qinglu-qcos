@echo off
rem ============================================================
rem  Remove QCOS auto-start task (use to disable/rollback)
rem  Cleans both the current task and any legacy task name.
rem ============================================================
schtasks /Delete /TN "QCOS_Server" /F
schtasks /Delete /TN "QCOS_AutoStart" /F
echo Auto-start tasks removed.
pause
