from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse

from ..browser.research import browser_source_text
from .models import Event, ResearchResult, Sport
from .sources import sources_for

_DATE_RE = re.compile(r"(?P<d>\d{1,2})[./-](?P<m>\d{1,2})[./-](?P<y>2026)")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -–—|·")


def _parse_khl(text: str, url: str, mode: str) -> list[Event]:
    lines = [_clean(x) for x in text.splitlines() if _clean(x)]
    events: list[Event] = []
    current_date: str | None = None
    separators = {"-", "–", "—"}

    for i, line in enumerate(lines):
        date_match = _DATE_RE.fullmatch(line)
        if date_match:
            current_date = f"{date_match.group('y')}-{int(date_match.group('m')):02d}-{int(date_match.group('d')):02d}"
            continue
        time_match = re.search(r"\b(\d{1,2}):(\d{2})\b", line)
        if not time_match or not current_date:
            continue

        candidates: list[str] = []
        for nxt in lines[i + 1 : i + 10]:
            if _DATE_RE.fullmatch(nxt) or _TIME_RE.search(nxt):
                break
            if nxt in {"Дата и время", "Хозяева", "Счет", "Гости"} or nxt in separators:
                continue
            candidates.append(nxt)
            if len(candidates) >= 2:
                break
        if len(candidates) < 2:
            continue

        home, away = candidates[0], candidates[1]
        if len(home) > 80 or len(away) > 80:
            continue
        if mode == "prematch" and current_date < datetime.now().strftime("%Y-%m-%d"):
            continue

        events.append(Event(
            sport="khl",
            mode=mode,  # type: ignore[arg-type]
            name=f"{home} — {away}",
            start_time=datetime.fromisoformat(f"{current_date}T{int(time_match.group(1)):02d}:{time_match.group(2)}"),
            status="LIVE" if mode == "live" else None,
            source="Browser Web Research",
            url=url,
            metadata={"source_domain": urlparse(url).netloc},
        ))
    return events


def _parse_esports(text: str, sport: Sport, url: str, mode: str) -> list[Event]:
    lines = [_clean(x) for x in text.splitlines() if _clean(x)]
    events: list[Event] = []
    current_date: str | None = None

    for i, line in enumerate(lines):
        match = re.search(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*-\s*(\d{4}-\d{2}-\d{2})", line)
        if match:
            current_date = match.group(1)
            continue
        match = re.search(r"(September|October|November|December)\s+(\d{1,2}),\s*(2026)", line)
        if match:
            months = {"September": 9, "October": 10, "November": 11, "December": 12}
            current_date = f"2026-{months[match.group(1)]:02d}-{int(match.group(2)):02d}"
            continue
        if not current_date:
            continue

        tm = re.search(r"\b(\d{1,2}:\d{2})\b", line)
        if not tm:
            continue
        rest = re.sub(r"^(bo\d+|Bo\d+)\s*", "", line[tm.end():].strip())
        if not rest:
            for nxt in lines[i + 1 : i + 4]:
                if nxt and not _TIME_RE.search(nxt):
                    rest = nxt
                    break
        if len(rest) < 3 or any(x in rest.lower() for x in ("matches for you", "event guide", "set filters")):
            continue
        if mode == "prematch" and current_date < datetime.now().strftime("%Y-%m-%d"):
            continue

        events.append(Event(
            sport=sport,
            mode=mode,  # type: ignore[arg-type]
            name=rest[:180],
            start_time=datetime.fromisoformat(f"{current_date}T{tm.group(1)}"),
            status="LIVE" if mode == "live" else None,
            source="Browser Web Research",
            url=url,
            metadata={"source_domain": urlparse(url).netloc},
        ))
    return events


async def collect(sport: Sport, mode: str) -> ResearchResult:
    events: list[Event] = []
    errors: list[str] = []
    for source in sources_for(sport):
        try:
            text = await browser_source_text(source.url)
            if sport == "khl":
                events.extend(_parse_khl(text, source.url, mode))
            else:
                events.extend(_parse_esports(text, sport, source.url, mode))
        except Exception as exc:
            errors.append(f"{source.name}: {type(exc).__name__}: {exc}")

    unique: dict[tuple[str, str], Event] = {}
    for event in events:
        unique[(event.name, event.url or "")] = event
    if not unique:
        errors.append(f"{sport}: real source pages produced no parseable {mode} events")
    return ResearchResult(events=list(unique.values()), errors=errors)
