from __future__ import annotations

from datetime import datetime

from ..browser.research import browser_search
from .catalog import SPORT_BY_KEY, SportConfig
from .models import Event, ResearchResult, Sport


async def collect(sport: Sport, mode: str) -> ResearchResult:
    config: SportConfig = SPORT_BY_KEY[sport]
    queries = config.prematch_queries if mode == "prematch" else config.live_queries
    events: list[Event] = []
    errors: list[str] = []

    for query in queries:
        try:
            results = await browser_search(query, engine="yandex")
            # Search results are evidence only. We do not turn arbitrary search
            # titles into fake matches; a dedicated parser must validate them.
            for item in results:
                events.append(
                    Event(
                        sport=sport,
                        mode=mode,  # type: ignore[arg-type]
                        name=item["title"],
                        source="Browser Web Research",
                        url=item["href"],
                        metadata={"query": query},
                    )
                )
        except Exception as exc:
            errors.append(f"{query}: {type(exc).__name__}: {exc}")

    # Deduplicate search hits by URL.
    unique: dict[str, Event] = {}
    for event in events:
        if event.url:
            unique[event.url] = event

    return ResearchResult(events=list(unique.values()), errors=errors)
