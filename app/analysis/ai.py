from __future__ import annotations

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError

from ..config import settings
from ..sports.models import Event


SYSTEM_PROMPT = """Ты спортивный аналитик. Работай только с переданными проверенными данными.
Не придумывай матчи, счёт, статистику или коэффициенты. Если данных недостаточно,
прямо укажи это. Отбирай только события с достаточным количеством подтверждений.
Для LIVE учитывай только фактически переданный текущий статус матча.
Давай практичный анализ: ключевые факторы, риски, наиболее интересные варианты.
Не выдумывай букмекерские коэффициенты. Если реальной линии нет в данных — так и скажи."""


async def analyze(events: list[Event]) -> str:
    if not settings.openai_api_key:
        return "AI-анализ недоступен: OPENAI_API_KEY не задан в .env"

    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or "https://api.openai.com/v1",
    )
    model = settings.openai_model or "gpt-6-astra"
    payload = [
        {
            "sport": event.sport,
            "mode": event.mode,
            "name": event.name,
            "status": event.status,
            "score": event.score,
            "source": event.source,
            "url": event.url,
            "metadata": event.metadata,
        }
        for event in events
    ]

    try:
        response = await client.responses.create(
            model=model,
            instructions=SYSTEM_PROMPT,
            input=str(payload),
        )
        return response.output_text
    except AuthenticationError:
        return (
            "AI-анализ недоступен: API отклонил ключ (401 Invalid token).\n\n"
            "Проверь OPENAI_API_KEY в локальном .env. BOT_TOKEN и OPENAI_API_KEY — это разные ключи."
        )
    except APIConnectionError:
        return "AI-анализ недоступен: нет соединения с AI API."
    except APIStatusError as exc:
        return f"AI-анализ недоступен: AI API вернул ошибку {exc.status_code}."
    except Exception as exc:
        return f"AI-анализ временно недоступен: {type(exc).__name__}: {exc}"
    finally:
        await client.close()
