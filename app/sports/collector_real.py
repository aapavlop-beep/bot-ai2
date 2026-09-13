from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from ..browser.research import browser_source_text
from .models import Event, ResearchResult, Sport
from .sources import sources_for

_DATE_RE = re.compile(r"(?P<d>\d{1,2})[./-](?P<m>\d{1,2})[./-](?P<y>20\d{2})")
_DATETIME_RE = re.compile(r"(?P<d>\d{1,2})[./-](?P<m>\d{1,2})[./-](?P<y>20\d{2})\s+(?P<h>\d{1,2}):(?P<min>\d{2})")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
_SCORE_RE = re.compile(r"\b\d{1,2}\s*[:\-]\s*\d{1,2}\b")
LIVE_WORDS = ("live", "в эфире", "сейчас", "1-й период", "2-й период", "3-й период", "1 период", "2 период", "3 период", "перерыв", "овертайм")
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

# Team names used by the Russian KHL calendar sources. We use this whitelist
# so navigation labels and unrelated page text can never become a fake team.
KHL_TEAMS = {
    "авангард": "Авангард",
    "автомобилист": "Автомобилист",
    "адмирал": "Адмирал",
    "амур": "Амур",
    "барыс": "Барыс",
    "динамо м": "Динамо Москва",
    "динамо москва": "Динамо Москва",
    "динамо мн": "Динамо Минск",
    "динамо минск": "Динамо Минск",
    "лада": "Лада",
    "локомотив": "Локомотив",
    "металлург мг": "Металлург Мг",
    "нефтехимик": "Нефтехимик",
    "салават юлаев": "Салават Юлаев",
    "северсталь": "Северсталь",
    "сибирь": "Сибирь",
    "ска": "СКА",
    "спартак": "Спартак",
    "торпедо": "Торпедо",
    "трактор": "Трактор",
    "цска": "ЦСКА",
    "сочи": "Сочи",
    "хк сочи": "Сочи",
    "ак барс": "Ак Барс",
    "шахтер": "Шахтер",
    "шанхай дрэгонс": "Шанхай Дрэгонс",
    "шанхайские драконы": "Шанхай Дрэгонс",
}


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
    window = " ".join(lines[max(0, index - 3): min(len(lines), index + 10)]).lower()
    has_live_word = any(word in window for word in LIVE_WORDS)
    score_match = _SCORE_RE.search(window)
    if not has_live_word:
        return False, None
    return True, score_match.group(0).replace(" ", "") if score_match else None


def _team_name(value: str) -> str | None:
    key = _clean(value).lower()
    return KHL_TEAMS.get(key)


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
    start_time = datetime.fromisoformat(f"{current_date}T{hour:02d}:{minute:02d}")
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
    """Parse real KHL calendar rows from Sports.ru and Championat.

    Both sources expose a date/time followed by home and away team names, but
    the exact HTML/text layout differs. We therefore locate the two next known
    KHL team names instead of relying on fixed positions in the DOM.
    """
    lines = [_clean(x) for x in text.splitlines() if _clean(x)]
    events: list[Event] = []
    now = _now()
    current_date: str | None = None

    for i, line in enumerate(lines):
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
            tm = re.search(r"\b(\d{1,2}):(\d{2})\b", line)
            if not tm:
                continue
            hour, minute = int(tm.group(1)), int(tm.group(2))

        if not current_date:
            continue

        teams: list[str] = []
        # A calendar row normally contains the two teams within the next few
        # lines. Stop at the next date/time so we cannot cross into another row.
        for nxt in lines[i + 1 : i + 18]:
            if _DATETIME_RE.search(nxt) or _DATE_RE.fullmatch(nxt):
                break
            team = _team_name(nxt)
            if team and team not in teams:
                teams.append(team)
            if len(teams) == 2:
                break

        if len(teams) != 2:
            continue

        event = _make_khl_event(
            current_date=current_date,
            hour=hour,
            minute=minute,
            home=teams[0],
            away=teams[1],
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
        match = re.search(r"(September|October|November|December)\s+(\d{1,2}),\s*(20\d{2})", line)
        if match:
            months = {"September": 9, "October": 10, "November": 11, "December": 12}
            current_date = f"{match.group(3)}-{months[match.group(1)]:02d}-{int(match.group(2)):02d}"
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
    sources = sources_for(sport, mode)

    for source in sources:
        try:
            text = await browser_source_text(source.url)
            text_len = len(text.strip())
            if text_len < 200:
                raise RuntimeError(f"source returned too little text: {text_len} chars")

            if sport == "khl":
                parsed = _parse_khl(text, source.url, mode)
            else:
                parsed = _parse_esports(text, sport, source.url, mode)

            events.extend(parsed)
            print(f"SOURCE OK: {source.name} text={text_len} parsed_{mode}={len(parsed)}")
        except Exception as exc:
            message = f"{source.name}: {type(exc).__name__}: {exc}"
            errors.append(message)
            print(f"SOURCE ERROR: {message}")

    unique: dict[tuple[str, str, str], Event] = {}
    for event in events:
        key = (event.name, event.start_time.isoformat() if event.start_time else "", event.mode)
        if key not in unique:
            unique[key] = event

    if not unique:
        errors.append(f"{sport}: real source pages produced no verified {mode} events")
    else:
        print(f"COLLECT OK: sport={sport} mode={mode} events={len(unique)}")

    return ResearchResult(events=list(unique.values()), errors=errors)
