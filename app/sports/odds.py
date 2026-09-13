from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

from ..browser.research import browser_search_pages, browser_source_text
from .models import Event

# Official Russian bookmaker domains. PARI is checked first because it is the
# primary Russian-line source for this project; others are fallbacks/comparison.
BOOKMAKERS: tuple[tuple[str, str, str], ...] = (
    ("PARI", "pari.ru", "https://pari.ru/sports/hockey/country/russia"),
    ("Фонбет", "fon.bet", "https://fon.bet/sports/hockey/country/russia"),
    ("Winline", "winline.ru", "https://winline.ru/stavki/sport/hockey"),
    ("BetBoom", "betboom.ru", "https://betboom.ru/sport/hockey"),
    ("Лига Ставок", "ligastavok.ru", "https://ligastavok.ru/bets/hockey"),
    ("БЕТСИТИ", "betcity.ru", "https://betcity.ru/ru/line/hockey"),
    ("Балтбет", "baltbet.ru", "https://baltbet.ru/line/hockey"),
)

_BAD_HOST_PARTS = {
    "yandex.ru",
    "ya.ru",
    "aistudio.yandex.ru",
    "yabs.yandex.ru",
    "passport.yandex.ru",
}

_ODDS_RE = re.compile(r"(?<!\d)(?:[1-9]\d?\.\d{2})(?!\d)")


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _bookmaker_for(url: str) -> str | None:
    host = _host(url)
    if host in _BAD_HOST_PARTS or host.endswith(".yandex.ru"):
        return None
    for name, domain, _ in BOOKMAKERS:
        if host == domain or host.endswith("." + domain):
            return name
    return None


def _team_tokens(event: Event) -> tuple[str, str]:
    parts = re.split(r"\s+[—–-]\s+", event.name, maxsplit=1)
    if len(parts) != 2:
        return event.name.lower(), ""
    return parts[0].strip().lower(), parts[1].strip().lower()


def _contains_match(text: str, event: Event) -> bool:
    low = text.lower().replace("ё", "е")
    home, away = _team_tokens(event)
    home = home.replace("ё", "е")
    away = away.replace("ё", "е")
    if home and away and home in low and away in low:
        return True
    home_words = [x for x in re.split(r"\s+", home) if len(x) >= 4]
    away_words = [x for x in re.split(r"\s+", away) if len(x) >= 4]
    return bool(home_words and away_words and any(x in low for x in home_words) and any(x in low for x in away_words))


def _odds_lines(text: str, event: Event) -> str:
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines()]
    lines = [x for x in lines if x]
    home, away = _team_tokens(event)
    home_words = [x for x in re.split(r"\s+", home) if len(x) >= 3]
    away_words = [x for x in re.split(r"\s+", away) if len(x) >= 3]

    anchors = (
        "исход", "итоговая победа", "победитель", "тоталы", "форы",
        "winner", "moneyline", "1 x 2", "победа", "тотал", "фора",
    )
    interesting: list[str] = []
    for i, line in enumerate(lines):
        low = line.lower()
        if any(anchor in low for anchor in anchors) or any(w in low for w in home_words + away_words):
            interesting.extend(lines[max(0, i - 2): min(len(lines), i + 8)])

    seen: set[str] = set()
    result: list[str] = []
    for line in interesting:
        if line in seen:
            continue
        seen.add(line)
        if len(line) <= 260:
            result.append(line)
        if len(result) >= 60:
            break

    compact = "\n".join(result)[:9000]
    if not _ODDS_RE.search(compact):
        return ""
    return compact


async def _direct_bookmaker(event: Event, bookmaker: str, domain: str, landing_url: str) -> tuple[str, str | None, list[str]]:
    try:
        text = await asyncio.wait_for(browser_source_text(landing_url, timeout_ms=10_000), timeout=11)
        if _bookmaker_for(landing_url) == bookmaker and _contains_match(text, event):
            compact = _odds_lines(text, event)
            if compact:
                return bookmaker, f"URL: {landing_url}\n{compact}", []
        return bookmaker, None, []
    except Exception as exc:
        return bookmaker, None, [f"{bookmaker}: direct page {type(exc).__name__}: {exc}"]


async def _collect_bookmaker(event: Event, bookmaker: str, domain: str, landing_url: str) -> tuple[str, str | None, list[str]]:
    direct_name, direct_text, direct_errors = await _direct_bookmaker(event, bookmaker, domain, landing_url)
    if direct_text:
        return direct_name, direct_text, direct_errors

    date_text = event.start_time.strftime("%d.%m.%Y") if event.start_time else ""
    errors = list(direct_errors)
    queries = (
        f'site:{domain} "{event.name}" {date_text}',
        f'site:{domain} "{_team_tokens(event)[0]}" "{_team_tokens(event)[1]}"',
    )
    for query in queries:
        try:
            pages = await asyncio.wait_for(browser_search_pages(query, max_pages=1), timeout=14)
        except Exception as exc:
            errors.append(f"{bookmaker}: {type(exc).__name__}: {exc}")
            continue
        for url, text in pages:
            if _bookmaker_for(url) != bookmaker or not _contains_match(text, event):
                continue
            compact = _odds_lines(text, event)
            if compact:
                return bookmaker, f"URL: {url}\n{compact}", errors
    return bookmaker, None, errors


async def collect_bookmaker_odds(event: Event) -> tuple[dict[str, str], list[str]]:
    """Find a real line from Russian bookmakers, preferring PARI and then fallbacks.

    One confirmed Russian bookmaker is enough to unlock market analysis. We do
    not waste time opening every bookmaker once a line is verified.
    """
    found: dict[str, str] = {}
    errors: list[str] = []
    # Check the primary source first, then fallbacks until a real line is found.
    for bookmaker, domain, landing_url in BOOKMAKERS:
        name, text, bookmaker_errors = await _collect_bookmaker(event, bookmaker, domain, landing_url)
        errors.extend(bookmaker_errors)
        if text:
            found[name] = text
            break
    return found, errors
