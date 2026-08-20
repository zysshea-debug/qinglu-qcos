@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ===== 注册 QCOS 每日自动备份计划任务 =====
REM 任务名: QCOS_Daily_Backup
REM 执行时间: 每天 12:30
REM 执行内容: daily_backup_runner.bat（无 pause，日志写 logs\daily_backup.log）

set TASK_NAME=QCOS_Daily_Backup
set TASK_CMD="%~dp0daily_backup_runner.bat"

echo 正在创建计划任务 %TASK_NAME% （每日 12:30）...
schtasks /Create /TN "%TASK_NAME%" /TR "%TASK_CMD%" /SC DAILY /ST 12:30 /F
if %errorlevel% neq 0 (
    echo [错误] 创建计划任务失败。
    echo 提示: 请右键本文件选择「以管理员身份运行」后重试。
    pause
    exit /b 1
)

echo.
echo [成功] 计划任务已创建，任务详情如下：
echo.
schtasks /Query /TN "%TASK_NAME%" /V /FO LIST
echo.
echo 说明: 任务每日 12:30 自动执行备份，即使当前没有用户登录界面也会运行。
echo 备份日志: %~dp0logs\daily_backup.log
pause
exit /b 0
