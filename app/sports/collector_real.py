from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from ..browser.research import browser_source_text
from .models import Event, ResearchResult, Sport
from .sources import sources_for

_DATE_RE = re.compile(r"(?P<d>\d{1,2})[./-](?P<m>\d{1,2})[./-](?P<y>2026)")
_DATETIME_RE = re.compile(r"(?P<d>\d{1,2})[./-](?P<m>\d{1,2})[./-](?P<y>2026)\s+(?P<h>\d{1,2}):(?P<min>\d{2})")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
_SCORE_RE = re.compile(r"\b\d{1,2}\s*[:\-]\s*\d{1,2}\b")
LIVE_WORDS = ("live", "в эфире", "сейчас", "1-й период", "2-й период", "3-й период", "1 период", "2 период", "3 период", "перерыв", "овертайм")
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -–—|·")


def _now() -> datetime:
    return datetime.now(MOSCOW_TZ).replace(tzinfo=None)


def _allowed_for_mode(start_time: datetime, mode: str, now: datetime, explicit_live: bool = False) -> bool:
    today = now.date()
    last_allowed = today + timedelta(days=2)

    if start_time.date() < today or start_time.date() > last_allowed:
        return False

    if mode == "prematch":
        return start_time > now

    if mode == "live":
        return explicit_live and start_time.date() == today

    return False


def _is_explicit_live(lines: list[str], index: int) -> tuple[bool, str | None]:
    window = " ".join(lines[max(0, index - 2): min(len(lines), index + 9)]).lower()
    has_live_word = any(word in window for word in LIVE_WORDS)
    score_match = _SCORE_RE.search(window)
    if not has_live_word:
        return False, None
    return True, score_match.group(0).replace(" ", "") if score_match else None


def _make_khl_event(
    current_date: str,
    hour: int,
    minute: int,
    home: str,
    away: str,
    mode: str,
    url: str,
    text: str,
    lines: list[str],
    index: int,
    now: datetime,
) -> Event | None:
    start_time = datetime.fromisoformat(
        f"{current_date}T{hour:02d}:{minute:02d}"
    )
    explicit_live, score = _is_explicit_live(lines, index)
    if not _allowed_for_mode(start_time, mode, now, explicit_live):
        return None

    return Event(
        sport="khl",
        mode=mode,  # type: ignore[arg-type]
        name=f"{home} — {away}",
        start_time=start_time,
        status="LIVE" if explicit_live else None,
        score=score,
        source="Browser Web Research",
        url=url,
        metadata={
            "source_domain": urlparse(url).netloc,
            "collector_source_url": url,
            "collector_source_text": text[:12000],
        },
    )


def _parse_khl(text: str, url: str, mode: str) -> list[Event]:
    """Parse the actual Sports.ru/Championat calendar layout.

    Sports.ru currently exposes matches as:
        14.09.2026 18:00
        Лада
        -
        -
        -
        Динамо Минск

    Older/alternate pages may put the date on its own line, so both layouts
    are supported. A match is accepted only if its date/time is in the
    requested window and, for LIVE, the page explicitly marks it as live.
    """
    lines = [_clean(x) for x in text.splitlines() if _clean(x)]
    events: list[Event] = []
    current_date: str | None = None
    separators = {"-", "–", "—"}
    now = _now()

    for i, line in enumerate(lines):
        # Current Sports.ru layout: date and time are on the same line.
        dt_match = _DATETIME_RE.search(line)
        if dt_match:
            current_date = (
                f"{dt_match.group('y')}-{int(dt_match.group('m')):02d}-{int(dt_match.group('d')):02d}"
            )
            hour = int(dt_match.group("h"))
            minute = int(dt_match.group("min"))
        else:
            date_match = _DATE_RE.fullmatch(line)
            if date_match:
                current_date = (
                    f"{date_match.group('y')}-{int(date_match.group('m')):02d}-{int(date_match.group('d')):02d}"
                )
                continue

            if not current_date:
                continue

            time_match = re.search(r"\b(\d{1,2}):(\d{2})\b", line)
            if not time_match:
                continue
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))

        if not current_date:
            continue

        candidates: list[str] = []
        for nxt in lines[i + 1 : i + 12]:
            if _DATE_RE.fullmatch(nxt) or _DATETIME_RE.search(nxt) or _TIME_RE.search(nxt):
                break
            if nxt in {"Дата и время", "Хозяева", "Счет", "Гости"} or nxt in separators:
                continue
            if _SCORE_RE.fullmatch(nxt) or nxt.lower() in {"завершен", "предстоящие", "live", "онлайн", "матч завершен"}:
                continue
            # Sports.ru may expose team names with harmless navigation text;
            # team names are short and do not contain these table markers.
            if len(nxt) > 80:
                continue
            candidates.append(nxt)
            if len(candidates) >= 2:
                break

        if len(candidates) < 2:
            continue

        home, away = candidates[0], candidates[1]
        event = _make_khl_event(
            current_date=current_date,
            hour=hour,
            minute=minute,
            home=home,
            away=away,
            mode=mode,
            url=url,
            text=text,
            lines=lines,
            index=i,
            now=now,
        )
        if event is not None:
            events.append(event)

    return events


def _parse_esports(text: str, sport: Sport, url: str, mode: str) -> list[Event]:
    lines = [_clean(x) for x in text.splitlines() if _clean(x)]
    events: list[Event] = []
    current_date: str | None = None
    now = _now()

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
            for nxt in lines[i + 1 : i + 5]:
                if nxt and not _TIME_RE.search(nxt):
                    rest = nxt
                    break
        if len(rest) < 3 or any(x in rest.lower() for x in ("matches for you", "event guide", "set filters")):
            continue

        start_time = datetime.fromisoformat(f"{current_date}T{tm.group(1)}")
        window = " ".join(lines[max(0, i - 2): min(len(lines), i + 7)]).lower()
        explicit_live = any(word in window for word in LIVE_WORDS) and bool(_SCORE_RE.search(window))
        if not _allowed_for_mode(start_time, mode, now, explicit_live):
            continue

        score_match = _SCORE_RE.search(window)
        events.append(Event(
            sport=sport,
            mode=mode,  # type: ignore[arg-type]
            name=rest[:180],
            start_time=start_time,
            status="LIVE" if explicit_live else None,
            score=score_match.group(0).replace(" ", "") if score_match else None,
            source="Browser Web Research",
            url=url,
            metadata={
                "source_domain": urlparse(url).netloc,
                "collector_source_url": url,
                "collector_source_text": text[:12000],
            },
        ))
    return events


async def collect(sport: Sport, mode: str) -> ResearchResult:
    events: list[Event] = []
    errors: list[str] = []
    for source in sources_for(sport, mode):
        try:
            text = await browser_source_text(source.url)
            if sport == "khl":
                events.extend(_parse_khl(text, source.url, mode))
            else:
                events.extend(_parse_esports(text, sport, source.url, mode))
        except Exception as exc:
            errors.append(f"{source.name}: {type(exc).__name__}: {exc}")

    unique: dict[tuple[str, str, str], Event] = {}
    for event in events:
        key = (event.name, event.start_time.isoformat() if event.start_time else "", event.mode)
        if key not in unique:
            unique[key] = event

    if not unique:
        errors.append(f"{sport}: real source pages produced no verified {mode} events")
    return ResearchResult(events=list(unique.values()), errors=errors)
