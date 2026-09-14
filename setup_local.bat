@echo off
setlocal EnableExtensions
cd /d "%~dp0"
py -3 setup_local.py
if errorlevel 1 (
  pause
  exit /b 1
)
echo.
echo Install dependencies: py -3 -m pip install -r requirements.txt
echo Install browser: py -3 -m playwright install chromium
pause
