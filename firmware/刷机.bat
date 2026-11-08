@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 刷入固件

echo ============================================================
echo  ESP32-C5 5GHz 信道普查仪 - 固件刷写
echo ============================================================
echo.
echo  刷写前请确认：
echo    1) 板子已用 USB 线连到电脑
echo    2) 没有别的程序占着串口（串口助手、Arduino IDE 等）
echo    3) 这一步会覆盖板子上的原有固件
echo.
echo  可用的串口：
echo.
python -c "from serial.tools import list_ports as lp; [print('   ', p.device, '-', p.description) for p in lp.comports()]" 2>nul
echo.
echo ------------------------------------------------------------
set /p PORT=请输入串口号（例如 COM9）: 

if "%PORT%"=="" (
  echo [错误] 没输入串口
  pause
  exit /b 1
)

echo.
echo 开始刷写 %PORT% ...
echo.

python -m esptool --chip esp32c5 -p %PORT% -b 921600 write-flash -z --flash-mode dio --flash-freq 80m --flash-size 32MB 0x0 "%~dp0Bruce-c5-n32r8-chan5g.bin"

if errorlevel 1 (
  echo.
  echo [失败] 见上方错误信息。常见原因：
  echo    - 串口号不对
  echo    - 没装 esptool：python -m pip install esptool
  echo    - 串口被占用
  echo.
  pause
  exit /b 1
)

echo.
echo [成功] 固件已写入。板子会自动重启。
echo        打开 ../pc/启动界面.bat 开始使用。
echo.
pause
