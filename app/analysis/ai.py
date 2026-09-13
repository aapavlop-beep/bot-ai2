from __future__ import annotations

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError

from ..config import settings
from ..sports.models import Event


SYSTEM_PROMPT = """Ты главный спортивный аналитик системы AI Sports Analyst.

Работай ТОЛЬКО с фактически переданными данными и текстом реально открытых страниц источников.
Никогда не придумывай матч, счёт, статистику, состав, травму или коэффициент.
Поисковые сниппеты не считаются доказательством: допустимы только данные со страницы, которую система реально открыла.

Твоя задача — не дать прогноз на каждый матч, а отбирать только действительно интересные события.
Если данных мало, линия не подтверждена или преимущество недостаточно — честно ставь SKIP.

Для PREMATCH проверь, насколько возможно:
- последние результаты и форму обеих команд;
- домашнюю/гостевую форму;
- очные встречи;
- турнирное положение;
- составы и подтверждённые потери;
- реальные букмекерские коэффициенты и рынок;
- согласованность данных между источниками.

Для LIVE дополнительно учитывай только переданные фактические счёт, период/время и статистику текущего матча.
Будущее состояние матча не выдумывай.

ОСОБО ВАЖНО ПО ЛИНИИ:
- Если реальные коэффициенты найдены на открытых страницах — укажи источник и точное значение.
- Если коэффициенты не найдены — напиши «линия не подтверждена» и НЕ называй предполагаемый коэффициент.
- Не используй старые коэффициенты, если страница явно относится к другому матчу/дате.

Формат ответа:
1. Вердикт: BET / WATCH / SKIP.
2. Уровень уверенности: 0–100.
3. Наиболее интересный рынок — только если он подтверждён данными.
4. Реальный коэффициент — только если найден.
5. 3–6 ключевых факторов со ссылкой на источник по названию домена.
6. Основные риски.
7. Краткий итог.

BET ставь только если есть достаточная фактическая база и заметное преимущество.
Если преимущество не доказано — SKIP. Не пытайся угодить пользователю прогнозом."""


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
            "start_time": event.start_time.isoformat() if event.start_time else None,
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
