@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 生成 5GHz 信道报告

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 找不到 python
  pause
  exit /b 1
)

echo ============================================================
echo  这个脚本会：
echo    1) 连上板子，跑一次三轮扫描（约 50 秒）
echo    2) 把结果取回来
echo    3) 生成 HTML 图表报告并自动打开
echo ============================================================
echo.

python chan5g_show.py
echo.
pause
