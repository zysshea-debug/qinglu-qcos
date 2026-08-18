@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ===== 1. 检测门店电脑是否装了 Python =====
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python。请先安装 Python 3.11+ 并务必勾选 "Add python.exe to PATH"。
    echo 下载地址： https://www.python.org/downloads/
    pause
    exit /b 1
)

REM ===== 2. 首次启动：建虚拟环境并安装依赖（仅需联网一次）=====
if not exist "venv" (
    echo 首次启动：正在创建虚拟环境并安装依赖（需联网，约 1-2 分钟）...
    python -m venv venv
    call "venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    echo 依赖安装完成。
) else (
    call "venv\Scripts\activate.bat"
)

REM ===== 2.5 首次启动：生成 .env 安全配置（若不存在）=====
if not exist ".env" (
    echo 首次启动：正在生成安全配置 .env ...
    python -c "import secrets; print('QCOS_SECRET_KEY=' + secrets.token_hex(32)); print('QCOS_ADMIN_PASSWORD=' + secrets.token_urlsafe(16))" > .env
    echo   .env 已生成（含 SECRET_KEY 和初始管理员密码，请妥善保管）
)

echo.
echo ============================================================
echo   青鹭 QCOS 正在启动...
echo   本机访问：   http://localhost:5000
echo   同WiFi设备： http://门店电脑IP:5000
echo   管理员登录： admin / （首次启动见控制台输出或 .env 文件）
echo   按 Ctrl+C 停止（程序崩溃会自动重启）
echo ============================================================
echo.

:loop
python app.py
echo [!] app.py 已退出，2 秒后自动重启...
timeout /t 2 >nul
goto loop
