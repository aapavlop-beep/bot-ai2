from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from .models import Event
from .odds import collect_bookmaker_odds
from ..browser.research import browser_search_pages, browser_source_text


MAX_PAGE_CHARS = 12_000
MAX_RESEARCH_PAGES_PER_QUERY = 2
RESEARCH_CONCURRENCY = 3
RESEARCH_TIMEOUT = 18
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

DIRECT_RESEARCH_URLS = {
    "khl": (
        "https://www.sports.ru/hockey/tournament/khl/calendar/",
        "https://www.championat.com/hockey/_superleague/tournament/7092/calendar/",
    ),
    "cs2": ("https://www.hltv.org/matches",),
    "dota2": ("https://liquipedia.net/dota2/Matches",),
}


def _valid_research_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return any(host == domain or host.endswith("." + domain) for domain in ALLOWED_RESEARCH_DOMAINS)


def _queries(event: Event) -> list[tuple[str, str]]:
    date_text = event.start_time.strftime("%d.%m.%Y") if event.start_time else ""
    name = f'"{event.name}"'
    if event.sport == "khl":
        return [
            ("form", f"{name} {date_text} последние матчи результаты форма домашние гостевые"),
            ("h2h", f"{name} очные встречи H2H последние матчи"),
            ("lineups_stats", f"{name} состав травмы потери вратарь статистика"),
        ]
    if event.sport == "cs2":
        return [
            ("form", f"{name} {date_text} последние матчи результаты форма рейтинг"),
            ("h2h", f"{name} H2H очные встречи"),
            ("lineups_stats", f"{name} состав игроки рейтинг карты статистика"),
        ]
    return [
        ("form", f"{name} {date_text} последние матчи результаты форма рейтинг"),
        ("h2h", f"{name} H2H очные встречи"),
        ("lineups_stats", f"{name} состав игроки draft статистика"),
    ]


async def _direct_source(url: str) -> tuple[str, str, str | None]:
    try:
        text = await asyncio.wait_for(browser_source_text(url, timeout_ms=12_000), timeout=13)
        if text and len(text.strip()) >= 200:
            return url, text, None
        return url, "", "direct source returned too little text"
    except Exception as exc:
        return url, "", f"{url}: {type(exc).__name__}: {exc}"


async def _collect_category(category: str, query: str, semaphore: asyncio.Semaphore) -> tuple[str, list[tuple[str, str]], list[str]]:
    async with semaphore:
        try:
            pages = await asyncio.wait_for(
                browser_search_pages(query, max_pages=MAX_RESEARCH_PAGES_PER_QUERY),
                timeout=RESEARCH_TIMEOUT,
            )
            return category, pages, []
        except Exception as exc:
            return category, [], [f"{category}: {type(exc).__name__}: {exc}"]


async def enrich_event(event: Event) -> Event:
    """Build a fact package from verified source pages and official Russian bookmaker pages."""
    evidence: dict[str, str] = {}
    source_urls: list[str] = []
    errors: list[str] = []

    # Critical: the collector already opened this page successfully. Reuse that
    # evidence instead of throwing it away and requiring a second browser fetch.
    seed_text = str(event.metadata.get("collector_source_text", "")).strip()
    seed_url = str(event.metadata.get("collector_source_url", event.url or "")).strip()
    if seed_text and seed_url and _valid_research_url(seed_url):
        domain = urlparse(seed_url).netloc.lower().removeprefix("www.")
        evidence[f"collector:{domain}:{urlparse(seed_url).path}"] = seed_text[:MAX_PAGE_CHARS]
        source_urls.append(seed_url)

    direct_urls = list(DIRECT_RESEARCH_URLS.get(event.sport, ()))
    if event.url and event.url not in direct_urls:
        direct_urls.insert(0, event.url)
    # Avoid refetching the exact page that collector already supplied.
    direct_urls = [url for url in direct_urls if url != seed_url]

    direct_results = await asyncio.gather(*(_direct_source(url) for url in direct_urls))
    for url, text, error in direct_results:
        if error:
            errors.append(error)
            continue
        if not _valid_research_url(url):
            continue
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        key = f"direct:{domain}:{urlparse(url).path}"
        evidence[key] = text.strip()[:MAX_PAGE_CHARS]
        source_urls.append(url)

    # Three focused searches are enough for the AI fact package and avoid the
    # previous six parallel Chromium launches that made Telegram appear stuck.
    semaphore = asyncio.Semaphore(RESEARCH_CONCURRENCY)
    results = await asyncio.gather(*(_collect_category(category, query, semaphore) for category, query in _queries(event)))

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
        bookmaker_odds, odds_errors = await asyncio.wait_for(collect_bookmaker_odds(event), timeout=45)
    except asyncio.TimeoutError:
        bookmaker_odds, odds_errors = {}, ["bookmakers: timeout after 45 seconds"]
    except Exception as exc:
        bookmaker_odds, odds_errors = {}, [f"bookmakers: {type(exc).__name__}: {exc}"]
    errors.extend(odds_errors)

    metadata = dict(event.metadata)
    metadata["research_collected"] = "true"
    metadata["research_page_count"] = str(len(evidence))
    metadata["research_source_count"] = str(len({urlparse(u).netloc.lower().removeprefix('www.') for u in source_urls}))
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
