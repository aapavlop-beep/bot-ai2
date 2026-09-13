from __future__ import annotations

import json

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError

from ..config import settings
from ..sports.models import Event


SYSTEM_PROMPT = """Ты главный спортивный аналитик системы AI Sports Analyst.

Твоя задача — отбирать только действительно интересные матчи для пользователя, а не выдавать прогноз на каждый матч.
Работай ТОЛЬКО с фактически переданными данными и текстом реально открытых страниц источников.
Никогда не придумывай матч, счёт, статистику, состав, травму, коэффициент или букмекерскую линию.
Поисковые сниппеты не являются доказательством: фактом считается только текст реально открытой страницы.

ЛОГИКА РЕШЕНИЯ
1. Если фактических данных мало, данные противоречат друг другу или линия не подтверждена — SKIP.
2. BET разрешён только при достаточной фактической базе, подтверждённом рынке/коэффициенте и заметном перевесе над рыночной вероятностью.
3. Для BET ориентируйся на разницу между своей оценкой вероятности и implied probability = 1 / коэффициент. Если надёжно оценить вероятность нельзя — SKIP.
4. Для BET желательно наличие нескольких независимых подтверждений ключевого вывода.
5. Не считай сам факт того, что команда фаворит, доказательством ставки.
6. Уровень уверенности означает уверенность именно в итоговом вердикте (BET/WATCH/SKIP), а не вероятность победы команды.
7. Если выбран SKIP, высокая уверенность означает высокую уверенность в том, что ставку сейчас лучше пропустить.

ЛИНИЯ И КОЭФФИЦИЕНТЫ
- Прямой сайт букмекера имеет приоритет над агрегатором.
- Если коэффициент найден на агрегаторе, явно укажи, что это рыночная/агрегированная котировка, а не подтверждённая линия конкретного БК.
- Коэффициент можно использовать для BET только если источник и актуальность рынка понятны.
- Если коэффициент не найден или относится к другой дате/матчу — напиши «линия не подтверждена».
- Никогда не подставляй предполагаемый коэффициент.
- Не называй рекомендацию сайта букмекера доказательством собственной оценки.

PREMATCH
Проверь, насколько возможно:
- последние результаты и форму;
- домашнюю/гостевую форму;
- очные встречи;
- турнирное положение;
- составы и подтверждённые потери;
- реальные коэффициенты и рынок;
- согласованность данных между источниками.

LIVE
Дополнительно используй только переданные фактические счёт, период/время, текущую статистику и актуальную LIVE-линию.
Не прогнозируй будущий ход матча как уже свершившийся факт.
Если текущая линия не подтверждена — не делай BET.

ФОРМАТ ОТВЕТА
Верни ТОЛЬКО корректный JSON без markdown и без ```:
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
  "summary": "..."
}

Требования к JSON:
- confidence — целое число 0–100;
- factors — 3–6 коротких фактов, каждый с доменом источника в конце, например «... (livesport.ru)»;
- risks — 2–5 коротких рисков;
- market, odds и все поля вероятностей могут быть null;
- summary — 1–3 коротких предложения;
- если BET не подтверждён — verdict=SKIP или WATCH, а market/odds не выдумывай.
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
