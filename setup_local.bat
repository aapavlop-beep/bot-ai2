@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo === BOT-AI2 LOCAL SETUP ===
echo.
echo This will create .env locally. It will NOT be uploaded to GitHub.
echo.
set /p "BOT_TOKEN=Telegram BOT_TOKEN: "
if "%BOT_TOKEN%"=="" goto :empty
set /p "OPENAI_API_KEY=OpenAI API key: "
if "%OPENAI_API_KEY%"=="" goto :empty
set /p "OPENAI_BASE_URL=OpenAI base URL (Enter to leave blank): "
set /p "OPENAI_MODEL=OpenAI model (Enter to leave blank): "
if "%OPENAI_MODEL%"=="" set "OPENAI_MODEL=gpt-6-astra"

> .env (
  echo BOT_TOKEN=%BOT_TOKEN%
  echo OPENAI_API_KEY=%OPENAI_API_KEY%
  echo OPENAI_BASE_URL=%OPENAI_BASE_URL%
  echo OPENAI_MODEL=%OPENAI_MODEL%
  echo LOG_LEVEL=INFO
  echo APP_ENV=development
  echo DATABASE_PATH=data/bot.db
)

echo.
echo .env created.
echo Install dependencies with:
echo python -m pip install -r requirements.txt

echo Install browser with:
echo python -m playwright install chromium
pause
exit /b 0

:empty
echo ERROR: BOT_TOKEN and OPENAI_API_KEY are required.
pause
exit /b 1
