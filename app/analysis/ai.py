from __future__ import annotations

from openai import AsyncOpenAI

from ..config import settings
from ..sports.models import Event


SYSTEM_PROMPT = """Ты спортивный аналитик. Работай только с переданными проверенными данными.
Не придумывай матчи, счёт, статистику или коэффициенты. Если данных недостаточно,
прямо укажи это. Отбирай только события с достаточным количеством подтверждений.
Для LIVE учитывай только фактически переданный текущий статус матча."""


async def analyze(events: list[Event]) -> str:
    if not settings.openai_api_key:
        return "AI_ANALYSIS_DISABLED: OPENAI_API_KEY не задан"

    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )
    model = settings.openai_model or "gpt-5.6"
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
    response = await client.responses.create(
        model=model,
        instructions=SYSTEM_PROMPT,
        input=str(payload),
    )
    return response.output_text
