from __future__ import annotations

import json

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError

from ..config import settings
from ..sports.models import Event


SYSTEM_PROMPT = """Ты главный спортивный аналитик системы AI Sports Analyst.

Цель — не прогнозировать каждый матч, а находить только лучшие подтверждённые возможности для ставки. Работай строго по переданным фактам и тексту реально открытых страниц.
Никогда не придумывай матч, счёт, статистику, состав, травму, коэффициент, рынок или букмекерскую линию.
Поисковые сниппеты не являются доказательством.

ЛОГИКА
1. Сначала проверь полноту данных. Для PREMATCH нужны по возможности: текущая форма обеих команд, домашняя/гостевая форма, H2H, турнирное положение, составы/потери, статистические показатели и актуальная линия российских БК.
2. Для LIVE дополнительно обязательны фактический текущий счёт, период/время игры, текущая статистика и актуальная LIVE-линия.
3. Если критически важные данные отсутствуют, противоречат друг другу или линия не подтверждена — BET запрещён.
4. BET разрешён только если есть достаточная фактическая база, конкретный подтверждённый рынок и коэффициент российского БК, а также заметный математический перевес.
5. implied probability = 1 / коэффициент. edge = estimated probability - implied probability. Не называй фаворита ставкой без доказанного edge.
6. confidence — уверенность именно в решении BET/WATCH/SKIP. Для SKIP высокая уверенность означает уверенность в том, что ставку сейчас лучше пропустить; это НЕ вероятность исхода.
7. Если линия отсутствует, не оценивай её по коэффициентам агрегаторов.

БУКМЕКЕРСКАЯ ЛИНИЯ
- Используй только поля metadata, начинающиеся с bookmaker_.
- Они считаются доказательством только если открытая страница официального российского БК явно содержит этот матч и реальные коэффициенты.
- Разрешённые источники: Фонбет, Winline, BetBoom, PARI, Лига Ставок, БЕТСИТИ, Балтбет и другие явно разрешённые российские БК.
- Не используй livesport.ru, Sports.ru, статьи, поисковую выдачу или агрегаторы как букмекерскую линию.
- Если bookmaker_count=0 — линия отсутствует и BET невозможен.
- Для LIVE нужна именно текущая LIVE-линия.

ТРЕБОВАНИЯ К АНАЛИЗУ
- Не ограничивайся общими фразами. Каждый фактор должен быть конкретным фактом: форма, H2H, турнир, составы, статистика или линия.
- Если данных по категории нет, явно укажи это в data_gaps, а не выдумывай значение.
- Не считай старые H2H сильнее свежей формы без объяснения.
- Учитывай размер выборки: несколько матчей начала сезона — слабая статистическая база.
- Учитывай противоречия между источниками.
- Для BET желательно несколько независимых источников, а не одна статья.
- WATCH используй, когда спортивная ситуация интересная, но прямо сейчас недостаточно подтверждения для ставки.
- SKIP используй, когда нет объективного преимущества или есть критический дефицит данных.

ФОРМАТ ОТВЕТА — ТОЛЬКО JSON
{
  "verdict": "BET|WATCH|SKIP",
  "confidence": 0,
  "market": "... или null",
  "odds": "... или null",
  "odds_source": "... или null",
  "estimated_probability": "... или null",
  "implied_probability": "... или null",
  "edge": "... или null",
  "data_quality": "HIGH|MEDIUM|LOW",
  "factors": ["..."],
  "risks": ["..."],
  "data_gaps": ["..."],
  "summary": "..."
}

Требования:
- confidence — целое 0–100;
- factors — 4–6 конкретных фактов, желательно покрывающих форму, H2H, турнир/статистику, составы и линию, когда эти данные реально есть;
- risks — 2–5 конкретных рисков;
- data_gaps — 0–6 реально отсутствующих важных блоков данных;
- market/odds/вероятности могут быть null только когда их нельзя надёжно подтвердить;
- если BET невозможен, не выдумывай market или odds;
- summary — 1–3 предложения с прямым объяснением решения.
"""


def _fallback(error: str) -> dict:
    return {
        "verdict": "SKIP",
        "confidence": 100,
        "market": None,
        "odds": None,
        "odds_source": None,
        "estimated_probability": None,
        "implied_probability": None,
        "edge": None,
        "data_quality": "LOW",
        "factors": [],
        "risks": [error],
        "data_gaps": ["AI-анализ не был завершён"],
        "summary": "Анализ не завершён. Ставка не формируется без подтверждённых данных.",
    }


def _parse_json(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].lstrip()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("AI returned non-object JSON")
    data.setdefault("verdict", "SKIP")
    data.setdefault("confidence", 0)
    data.setdefault("market", None)
    data.setdefault("odds", None)
    data.setdefault("odds_source", None)
    data.setdefault("estimated_probability", None)
    data.setdefault("implied_probability", None)
    data.setdefault("edge", None)
    data.setdefault("data_quality", "LOW")
    data.setdefault("factors", [])
    data.setdefault("risks", [])
    data.setdefault("data_gaps", [])
    data.setdefault("summary", "")
    return data


async def analyze(events: list[Event]) -> dict:
    if not settings.openai_api_key:
        return _fallback("OPENAI_API_KEY не задан в .env")

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
            input=json.dumps(payload, ensure_ascii=False),
        )
        return _parse_json(response.output_text)
    except AuthenticationError:
        return _fallback("AI API отклонил ключ: 401 Invalid token. Проверь OPENAI_API_KEY и не путай его с BOT_TOKEN.")
    except APIConnectionError:
        return _fallback("Нет соединения с AI API.")
    except APIStatusError as exc:
        return _fallback(f"AI API вернул ошибку {exc.status_code}.")
    except (json.JSONDecodeError, ValueError) as exc:
        return _fallback(f"AI вернул некорректный структурированный ответ: {exc}.")
    except Exception as exc:
        return _fallback(f"AI-анализ временно недоступен: {type(exc).__name__}: {exc}")
    finally:
        await client.close()
