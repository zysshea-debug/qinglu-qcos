@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ===== 手动备份 QCOS 生产数据库 =====
REM 双击运行即可。调用项目 venv 的 python，不依赖系统全局 python。

if not exist "venv\Scripts\python.exe" (
    echo [错误] VENV_NOT_FOUND
    echo 未找到项目虚拟环境: %~dp0venv\Scripts\python.exe
    echo 请先运行 启动.bat 完成首次安装后再备份。
    pause
    exit /b 1
)

echo ============================================================
echo   青鹭 QCOS 数据库备份
echo ============================================================
echo.

"%~dp0venv\Scripts\python.exe" "%~dp0backup_qcos.py"
set RC=%errorlevel%

echo.
if %RC%==0 (
    echo 数据库备份完成。
    echo 备份文件位于 backups\ 目录。
) else (
    echo [错误] 数据库备份失败！
    echo 请检查上方错误信息，确认 qcos.db 是否正常。
)
pause
exit /b %RC%
