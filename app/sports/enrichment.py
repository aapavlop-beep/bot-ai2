from __future__ import annotations

from urllib.parse import urlparse

from .models import Event
from ..browser.research import browser_search_pages


MAX_PAGE_CHARS = 14000


def _queries(event: Event) -> list[tuple[str, str]]:
    date_text = event.start_time.strftime("%d.%m.%Y") if event.start_time else ""
    mode = event.mode.upper()
    return [
        (
            "match",
            f'"{event.name}" {date_text} статистика последние матчи состав травмы {mode}',
        ),
        (
            "odds",
            f'"{event.name}" {date_text} коэффициенты линия Winline Fonbet BetBoom Parimatch',
        ),
        (
            "form",
            f'"{event.name}" {date_text} результаты H2H форма таблица',
        ),
    ]


async def enrich_event(event: Event) -> Event:
    """Collect actual source-page evidence for one selected event.

    Search results are used only to discover URLs. Evidence is accepted only
    after the browser opens the discovered source page and reads its visible
    text. Search snippets themselves are never sent to the AI as facts.
    """
    evidence: dict[str, str] = {}
    source_urls: list[str] = []
    errors: list[str] = []

    for category, query in _queries(event):
        try:
            pages = await browser_search_pages(query, max_pages=4)
        except Exception as exc:
            errors.append(f"{category}: {type(exc).__name__}: {exc}")
            continue

        for url, text in pages:
            if not text.strip():
                continue
            domain = urlparse(url).netloc.lower()
            key = f"{category}:{domain}"
            if key in evidence:
                continue
            cleaned = text.strip()
            evidence[key] = cleaned[:MAX_PAGE_CHARS]
            source_urls.append(url)

    metadata = dict(event.metadata)
    metadata["research_collected"] = "true"
    metadata["research_page_count"] = str(len(evidence))
    if source_urls:
        metadata["research_urls"] = "\n".join(source_urls[:20])
    if errors:
        metadata["research_errors"] = "\n".join(errors)

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
