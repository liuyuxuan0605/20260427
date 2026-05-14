@echo off
chcp 65001 >nul 2>&1
title 音乐推荐播放器

echo ========================================
echo   🎵 音乐推荐播放器
echo ========================================
echo.
echo   1. 启动服务器
echo   2. 批量导入歌曲数据（首次/补充）
echo   3. 退出
echo.
set /p choice=请选择 (1/2/3): 

if "%choice%"=="2" goto IMPORT
if "%choice%"=="3" exit /b 0

:START
REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未检测到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

REM 检查虚拟环境
if not exist "venv\Scripts\activate.bat" (
    echo 📦 创建虚拟环境...
    python -m venv venv
    call venv\Scripts\activate.bat
    echo 📦 安装依赖（使用清华源）...
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
) else (
    call venv\Scripts\activate.bat
)

REM 创建数据目录
if not exist "data" mkdir data
if not exist "data\covers" mkdir data\covers

echo.
echo 🚀 启动服务器...
echo 📍 访问地址: http://127.0.0.1:5000
echo.
python app.py
pause
exit /b 0

:IMPORT
if not exist "venv\Scripts\activate.bat" (
    echo 📦 请先运行选项1创建虚拟环境
    pause
    exit /b 1
)
call venv\Scripts\activate.bat
echo.
echo 📥 开始批量导入歌曲数据（需要几分钟）...
python bulk_import.py
echo.
echo ✅ 导入完成！
pause
goto START
