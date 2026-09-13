from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from .models import Event
from .odds import collect_bookmaker_odds
from ..browser.research import browser_search_pages


MAX_PAGE_CHARS = 14_000
MAX_RESEARCH_PAGES_PER_QUERY = 3
RESEARCH_CONCURRENCY = 4
ALLOWED_RESEARCH_DOMAINS = {
    "sports.ru",
    "championat.com",
    "livesport.ru",
    "pressball.by",
    "hockey.ru",
    "khl.ru",
    "hltv.org",
    "liquipedia.net",
}


def _valid_research_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return any(host == domain or host.endswith("." + domain) for domain in ALLOWED_RESEARCH_DOMAINS)


def _queries(event: Event) -> list[tuple[str, str]]:
    date_text = event.start_time.strftime("%d.%m.%Y") if event.start_time else ""
    mode = event.mode.upper()
    name = f'"{event.name}"'
    if event.sport == "khl":
        return [
            ("match", f"{name} {date_text} матч КХЛ составы стартовые пятерки {mode}"),
            ("form", f"{name} последние 5 матчей результаты форма {mode}"),
            ("h2h", f"{name} очные встречи H2H последние матчи"),
            ("table", f"{name} КХЛ турнирная таблица положение конференция"),
            ("lineups", f"{name} состав травмы потери дисквалификации новости"),
            ("stats", f"{name} статистика шайбы голы вратари большинство меньшинство"),
        ]
    if event.sport == "cs2":
        return [
            ("match", f"{name} {date_text} CS2 матч составы {mode}"),
            ("form", f"{name} последние матчи результаты форма рейтинг"),
            ("h2h", f"{name} H2H очные встречи"),
            ("stats", f"{name} статистика карты игроки рейтинг"),
            ("lineups", f"{name} состав замены stand-in новости"),
        ]
    return [
        ("match", f"{name} {date_text} Dota 2 матч составы {mode}"),
        ("form", f"{name} последние матчи результаты форма рейтинг"),
        ("h2h", f"{name} H2H очные встречи"),
        ("stats", f"{name} статистика карты игроки draft"),
        ("lineups", f"{name} состав замены новости"),
    ]


async def _collect_category(
    category: str,
    query: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, list[tuple[str, str]], list[str]]:
    async with semaphore:
        try:
            pages = await asyncio.wait_for(
                browser_search_pages(query, max_pages=MAX_RESEARCH_PAGES_PER_QUERY),
                timeout=25,
            )
            return category, pages, []
        except Exception as exc:
            return category, [], [f"{category}: {type(exc).__name__}: {exc}"]


async def enrich_event(event: Event) -> Event:
    """Collect sports evidence and verified Russian bookmaker lines.

    Research categories run concurrently and each category has a hard timeout.
    A slow/unavailable source is isolated instead of blocking the whole match.
    """
    evidence: dict[str, str] = {}
    source_urls: list[str] = []
    errors: list[str] = []

    semaphore = asyncio.Semaphore(RESEARCH_CONCURRENCY)
    tasks = [
        _collect_category(category, query, semaphore)
        for category, query in _queries(event)
    ]
    results = await asyncio.gather(*tasks)

    for category, pages, category_errors in results:
        errors.extend(category_errors)
        for url, text in pages:
            if not _valid_research_url(url) or not text.strip():
                continue
            domain = urlparse(url).netloc.lower().removeprefix("www.")
            key = f"{category}:{domain}:{urlparse(url).path}"
            if key in evidence:
                continue
            evidence[key] = text.strip()[:MAX_PAGE_CHARS]
            source_urls.append(url)

    try:
        bookmaker_odds, odds_errors = await asyncio.wait_for(
            collect_bookmaker_odds(event),
            timeout=45,
        )
    except asyncio.TimeoutError:
        bookmaker_odds, odds_errors = {}, ["bookmakers: timeout after 45 seconds"]
    except Exception as exc:
        bookmaker_odds, odds_errors = {}, [f"bookmakers: {type(exc).__name__}: {exc}"]
    errors.extend(odds_errors)

    metadata = dict(event.metadata)
    metadata["research_collected"] = "true"
    metadata["research_page_count"] = str(len(evidence))
    metadata["research_source_count"] = str(len({urlparse(u).netloc.lower().removeprefix('www.') for u in source_urls}))
    if source_urls:
        metadata["research_urls"] = "\n".join(source_urls[:30])

    metadata["bookmaker_count"] = str(len(bookmaker_odds))
    metadata["bookmaker_names"] = ", ".join(bookmaker_odds.keys())
    for bookmaker, text in bookmaker_odds.items():
        safe_key = bookmaker.lower().replace(" ", "_")
        metadata[f"bookmaker_{safe_key}"] = text

    if errors:
        metadata["research_errors"] = "\n".join(errors[:30])

    for index, (key, text) in enumerate(evidence.items(), start=1):
        metadata[f"research_{index}_{key}"] = text

    return Event(
        sport=event.sport,
        mode=event.mode,
        name=event.name,
        start_time=event.start_time,
        status=event.status,
        score=event.score,
        source=event.source,
        url=event.url,
        metadata=metadata,
    )
