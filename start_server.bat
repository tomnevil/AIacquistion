@echo off
title AI-Acquisition Server (后台运行，勿关闭)
cd /d "%~dp0"
echo ================================
echo   AI 获客系统 - 后台服务
echo ================================
echo.
echo 服务地址: http://127.0.0.1:8000
echo.
echo 请不要关闭此窗口！
echo 按 Ctrl+C 可停止服务
echo ================================
echo.

:loop
python main.py
echo.
echo [%date% %time%] 服务已停止，5秒后自动重启...
timeout /t 5 /nobreak >nul
goto loop
