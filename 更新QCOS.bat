@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ===== 一键安全更新 QCOS（Git pull + 备份 + 迁移 + 重启）=====
REM 双击运行。所有复杂逻辑在 update_qcos.py 中执行，本 BAT 只做入口。

if not exist "venv\Scripts\python.exe" (
    echo [错误] VENV_NOT_FOUND
    echo 未找到项目虚拟环境: %~dp0venv\Scripts\python.exe
    echo 请先运行 启动.bat 完成首次安装后再更新。
    pause
    exit /b 1
)

echo ============================================================
echo   青鹭 QCOS 一键安全更新
echo ============================================================
echo.

"%~dp0venv\Scripts\python.exe" "%~dp0update_qcos.py"
set RC=%errorlevel%

echo.
if %RC%==0 (
    echo 更新完成。
) else (
    echo [错误] 更新失败，请查看上方输出定位失败步骤。
    echo 数据库与备份均被保留，未自动回滚。
)
pause
exit /b %RC%
