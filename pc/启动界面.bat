@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 5GHz 信道普查仪

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo [错误] 找不到 python，请先安装 Python 3.8 或更新版本
  echo        安装时务必勾选 "Add Python to PATH"
  echo.
  pause
  exit /b 1
)

python -c "import serial" >nul 2>nul
if errorlevel 1 (
  echo.
  echo [提示] 缺少 pyserial，正在安装...
  python -m pip install pyserial
  echo.
)

echo 正在启动界面...
python chan5g_gui.py
if errorlevel 1 (
  echo.
  echo [错误] 界面异常退出，上面有错误信息
  pause
)
