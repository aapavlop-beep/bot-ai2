@echo off
cd /d "%~dp0"

if not exist .env (
  echo .env not found. Run setup_local.bat first.
  pause
  exit /b 1
)

if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -m app.main
) else (
  py -3 -m app.main
)
pause
