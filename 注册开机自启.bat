@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "SCRIPT=%~dp0启动.bat"

echo 将以 SYSTEM 账户注册开机自启任务：QCOS_AutoStart
echo 作用：门店电脑开机（无需任何人登录）即自动启动青鹭QCOS，崩溃也会自动重启。
echo.

schtasks /Create /TN "QCOS_AutoStart" /TR "\"%SCRIPT%\"" /SC ONSTART /RU SYSTEM /RL HIGHEST /F
if errorlevel 1 (
    echo [失败] 请右键本文件 -> "以管理员身份运行" 后再试。
    pause
    exit /b 1
)

echo [成功] 已注册。下次门店电脑开机将自动运行 启动.bat。
echo 如需取消自启： schtasks /Delete /TN "QCOS_AutoStart" /F
echo.
pause
