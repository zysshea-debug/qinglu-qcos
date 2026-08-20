@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ===== QCOS 每日自动备份执行器（计划任务用，无 pause）=====
REM 输出追加写入 logs\daily_backup.log

if not exist "logs" mkdir "logs"

echo [%date% %time%] ===== QCOS Daily Backup Start =====>> "%~dp0logs\daily_backup.log"
"%~dp0venv\Scripts\python.exe" "%~dp0backup_qcos.py" >> "%~dp0logs\daily_backup.log" 2>&1
set RC=%errorlevel%
echo [%date% %time%] Backup exit code: %RC%>> "%~dp0logs\daily_backup.log"
exit /b %RC%
