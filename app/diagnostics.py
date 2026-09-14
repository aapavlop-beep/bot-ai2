"""Read-only connectivity checks. Never prints credentials or response bodies.

Run `py -3 -m app.diagnostics` to check Telegram identity (no messages sent).
Use `--ai` to also make one small billable structured-output API request.
"""
import argparse
import asyncio
import json

from aiogram import Bot
from openai import AsyncOpenAI

from .config import configuration_warnings, settings
from .telegram_session import telegram_session


async def check(include_ai: bool = False) -> bool:
    ok = True
    for warning in configuration_warnings():
        print(f"Config: WARNING ({warning})")
    if not settings.bot_token:
        print("Telegram: BOT_TOKEN missing")
        ok = False
    else:
        try:
            async with Bot(settings.bot_token, session=telegram_session()) as bot:
                profile = await asyncio.wait_for(bot.get_me(), 15)
                print(f"Telegram: OK (@{profile.username})")
        except Exception as exc:
            print(f"Telegram: FAILED ({type(exc).__name__})")
            ok = False
    if include_ai:
        if not settings.openai_api_key:
            print("AI: OPENAI_API_KEY missing")
            return False
        if settings.openai_api_key == settings.bot_token:
            print("AI: OPENAI_API_KEY equals BOT_TOKEN; a Windows environment variable may be overriding .env. Configure a separate AI service key and restart the terminal.")
            return False
        try:
            async with AsyncOpenAI(api_key=settings.openai_api_key,
                                   base_url=settings.openai_base_url or "https://api.openai.com/v1",
                                   timeout=30, max_retries=0) as client:
                response = await client.responses.create(
                    model=settings.openai_model or "gpt-6-astra",
                    input='Return a JSON object with status equal to "ok".',
                    max_output_tokens=256,
                    text={"format": {"type": "json_schema", "name": "connection_check", "strict": True,
                                     "schema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["ok"]}},
                                                "required": ["status"], "additionalProperties": False}}},
                )
                if response.status != "completed" or json.loads(response.output_text) != {"status": "ok"}:
                    print("AI: FAILED (incomplete or invalid structured response)")
                    ok = False
                else:
                    print("AI: OK (Responses API + structured output)")
        except Exception as exc:
            print(f"AI: FAILED ({type(exc).__name__}, status={getattr(exc, 'status_code', 'n/a')})")
            ok = False
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ai", action="store_true", help="Make one small billable AI request")
    raise SystemExit(0 if asyncio.run(check(parser.parse_args().ai)) else 1)
