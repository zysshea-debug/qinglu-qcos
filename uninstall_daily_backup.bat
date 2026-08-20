@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ===== 删除 QCOS 每日自动备份计划任务 =====

set TASK_NAME=QCOS_Daily_Backup

echo 正在删除计划任务 %TASK_NAME% ...
schtasks /Delete /TN "%TASK_NAME%" /F
if %errorlevel% neq 0 (
    echo [信息] 任务不存在或已删除。
) else (
    echo [成功] 计划任务已删除。
)
pause
exit /b 0
