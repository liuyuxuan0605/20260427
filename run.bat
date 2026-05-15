@echo off
chcp 65001 >nul 2>&1
title 音乐推荐播放器

echo ========================================
echo   🎵 音乐推荐播放器 启动中...
echo ========================================
echo.

REM 激活虚拟环境
if not exist "venv\Scripts\python.exe" (
    echo ❌ 未找到虚拟环境，请先运行 start.bat 初始化
    pause
    exit /b 1
)

REM 创建必要目录
if not exist "data" mkdir data
if not exist "data\covers" mkdir data\covers

echo 🚀 启动服务器...
echo 📍 访问地址: http://127.0.0.1:5000
echo 📍 按 Ctrl+C 停止服务器
echo.

venv\Scripts\python.exe app.py
pause
