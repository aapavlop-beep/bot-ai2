from __future__ import annotations

from urllib.parse import urlparse

from .models import Event
from .odds import collect_bookmaker_odds
from ..browser.research import browser_search_pages


MAX_PAGE_CHARS = 14000
ALLOWED_RESEARCH_DOMAINS = {
    "sports.ru",
    "championat.com",
    "livesport.ru",
    "pressball.by",
    "hockey.ru",
    "khl.ru",
    "hltv.org",
    "liquipedia.net",
    "liquipedia.net",
}


def _valid_research_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return any(host == domain or host.endswith("." + domain) for domain in ALLOWED_RESEARCH_DOMAINS)


def _queries(event: Event) -> list[tuple[str, str]]:
    date_text = event.start_time.strftime("%d.%m.%Y") if event.start_time else ""
    mode = event.mode.upper()
    return [
        (
            "match",
            f'"{event.name}" {date_text} статистика последние матчи состав травмы {mode}',
        ),
        (
            "form",
            f'"{event.name}" {date_text} результаты H2H форма таблица',
        ),
    ]


async def enrich_event(event: Event) -> Event:
    """Collect only opened, validated sports pages plus official RU bookmaker lines."""
    evidence: dict[str, str] = {}
    source_urls: list[str] = []
    errors: list[str] = []

    for category, query in _queries(event):
        try:
            pages = await browser_search_pages(query, max_pages=5)
        except Exception as exc:
            errors.append(f"{category}: {type(exc).__name__}: {exc}")
            continue

        for url, text in pages:
            if not _valid_research_url(url) or not text.strip():
                continue
            domain = urlparse(url).netloc.lower().removeprefix("www.")
            key = f"{category}:{domain}"
            if key in evidence:
                continue
            evidence[key] = text.strip()[:MAX_PAGE_CHARS]
            source_urls.append(url)

    bookmaker_odds, odds_errors = await collect_bookmaker_odds(event)
    errors.extend(odds_errors)

    metadata = dict(event.metadata)
    metadata["research_collected"] = "true"
    metadata["research_page_count"] = str(len(evidence))
    if source_urls:
        metadata["research_urls"] = "\n".join(source_urls[:20])

    # The AI receives bookmaker evidence separately so it cannot confuse a
    # general sports article with an actual betting line.
    metadata["bookmaker_count"] = str(len(bookmaker_odds))
    metadata["bookmaker_names"] = ", ".join(bookmaker_odds.keys())
    for bookmaker, text in bookmaker_odds.items():
        safe_key = bookmaker.lower().replace(" ", "_")
        metadata[f"bookmaker_{safe_key}"] = text

    if errors:
        metadata["research_errors"] = "\n".join(errors[:20])

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
