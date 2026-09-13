@echo off
cd /d "%~dp0"

if not exist .env (
  echo .env not found. Run setup_local.bat first.
  pause
  exit /b 1
)

python -m app.main
pause
