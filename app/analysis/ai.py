from __future__ import annotations

import json

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError

from ..config import settings
from ..sports.models import Event


SYSTEM_PROMPT = """Ты главный спортивный аналитик системы AI Sports Analyst.

Цель — не прогнозировать каждый матч, а находить только лучшие подтверждённые возможности для ставки. Работай строго по фактам из реально открытых страниц.
Никогда не придумывай матч, счёт, статистику, состав, травму, коэффициент, рынок или букмекерскую линию.
Поисковые сниппеты не являются доказательством.

ЛОГИКА
1. Сначала оцени полноту данных. PREMATCH: свежая форма обеих команд, домашняя/гостевая форма, H2H, турнирное положение, составы/потери, статистика и актуальная линия российских БК.
2. LIVE: дополнительно обязательны фактический счёт, период/время игры, текущая статистика и актуальная LIVE-линия.
3. Если критически важные данные отсутствуют, противоречат друг другу или линия не подтверждена — BET запрещён.
4. BET разрешён только при достаточной фактической базе, конкретном подтверждённом рынке/коэффициенте российского БК и заметном математическом перевесе.
5. implied probability = 1 / коэффициент. edge = estimated probability - implied probability. Фаворит сам по себе не является ставкой.
6. confidence — уверенность именно в решении BET/WATCH/SKIP, а НЕ вероятность исхода.
7. При LOW качестве данных confidence обычно 80–92. Никогда не ставь 98–100 только потому, что данных мало или линия отсутствует.
8. Если источники расходятся по ключевому факту, спорный факт исключи из факторов, укажи расхождение в risks/data_gaps и снизь data_quality.
9. Если research_page_count=0, считай спортивное исследование НЕ выполненным. Не выдавай общие знания за факты. В этом случае verdict=SKIP, data_quality=LOW.
10. Если bookmaker_count=0, verdict не может быть BET. Если спортивные данные интересны, но линии нет, можно выбрать WATCH; иначе SKIP.

БУКМЕКЕРСКАЯ ЛИНИЯ
- Используй только metadata, начинающиеся с bookmaker_.
- Подтверждённой считается только открытая страница официального российского БК, где явно видны нужный матч и реальные коэффициенты.
- Разрешённые источники: Фонбет, Winline, BetBoom, PARI, Лига Ставок, БЕТСИТИ, Балтбет и другие явно разрешённые российские БК.
- Не используй Sports.ru, Чемпионат, LiveSport, Pressball, статьи, поисковую выдачу или агрегаторы как букмекерскую линию.
- bookmaker_count=0 означает, что подтверждённой линии нет и BET невозможен.
- Для LIVE нужна текущая LIVE-линия, а не prematch-коэффициент.

ПОЛНОТА АНАЛИЗА
Используй только реально присутствующие в metadata research_* и bookmaker_* данные.
Старайся покрыть отдельными фактами:
• Форма — последние матчи и результаты.
• Дом/гости — если источник даёт такую статистику.
• H2H — свежие очные встречи с учётом давности.
• Турнир — место, очки, разница/форма в турнире.
• Составы — подтверждённые потери, вратарь/игроки, если данные есть.
• Статистика — голы/шайбы, броски, большинство/меньшинство или релевантные показатели конкретного спорта.
• Линия — конкретный рынок, коэффициент, БК и расчёт implied probability/edge.
Если блок реально не найден — перечисли его в data_gaps. Не заполняй пробел общими догадками.

ОТБОР
- WATCH: спортивная ситуация интересная, но данных/линии пока недостаточно для ставки.
- SKIP: нет объективного преимущества, критические данные отсутствуют либо рынок не подтверждён.
- BET: только когда фактическая база и рынок действительно подтверждены.

ФОРМАТ — ТОЛЬКО JSON
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
- factors — 4–6 конкретных фактов только если они реально подтверждены; при нехватке данных лучше 1–3 факта, чем выдуманные;
- risks — 2–5 конкретных рисков или противоречий;
- data_gaps — 0–6 реально отсутствующих важных блоков;
- market/odds/вероятности null, если они не подтверждены;
- summary — 1–3 предложения с прямым объяснением решения.
"""


def _fallback(error: str) -> dict:
    return {
        "verdict": "SKIP",
        "confidence": 90,
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


def _normalize(data: dict, event: Event) -> dict:
    result = dict(data)
    try:
        confidence = max(0, min(100, int(result.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0

    quality = str(result.get("data_quality") or "LOW").upper()
    research_pages = int(event.metadata.get("research_page_count", "0") or 0)
    bookmaker_count = int(event.metadata.get("bookmaker_count", "0") or 0)

    if research_pages == 0:
        quality = "LOW"
        result["verdict"] = "SKIP"
        confidence = min(confidence, 90)
    elif quality == "LOW":
        confidence = min(confidence, 92)

    if bookmaker_count == 0 and str(result.get("verdict", "SKIP")).upper() == "BET":
        result["verdict"] = "WATCH"
        confidence = min(confidence, 88)
        result["market"] = None
        result["odds"] = None
        result["odds_source"] = None
        result["estimated_probability"] = None
        result["implied_probability"] = None
        result["edge"] = None

    result["confidence"] = confidence
    result["data_quality"] = quality
    return result


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
        parsed = _parse_json(response.output_text)
        return _normalize(parsed, events[0]) if events else parsed
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
