from __future__ import annotations

import re
from urllib.parse import urlparse

from ..browser.research import browser_search_pages
from .models import Event

# Only official Russian bookmaker domains are accepted as odds evidence.
BOOKMAKERS: tuple[tuple[str, str], ...] = (
    ("Фонбет", "fon.bet"),
    ("Winline", "winline.ru"),
    ("BetBoom", "betboom.ru"),
    ("PARI", "pari.ru"),
    ("Лига Ставок", "ligastavok.ru"),
    ("БЕТСИТИ", "betcity.ru"),
    ("Балтбет", "baltbet.ru"),
)

_BAD_HOST_PARTS = {
    "yandex.ru",
    "ya.ru",
    "aistudio.yandex.ru",
    "yabs.yandex.ru",
    "passport.yandex.ru",
}


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _bookmaker_for(url: str) -> str | None:
    host = _host(url)
    if host in _BAD_HOST_PARTS or host.endswith(".yandex.ru"):
        return None
    for name, domain in BOOKMAKERS:
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
    return bool(home and away and home in low and away in low)


def _odds_lines(text: str, event: Event) -> str:
    """Extract a compact, factual odds window for the AI.

    We intentionally keep the original bookmaker text around the market labels
    instead of guessing which number belongs to which market.
    """
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines()]
    lines = [x for x in lines if x]
    home, away = _team_tokens(event)
    home_words = [x for x in re.split(r"\s+", home) if len(x) >= 3]
    away_words = [x for x in re.split(r"\s+", away) if len(x) >= 3]

    anchors = ("исход", "итоговая победа", "победитель", "тоталы", "форы", "winner", "moneyline", "1 x 2")
    interesting: list[str] = []
    for i, line in enumerate(lines):
        low = line.lower()
        if any(anchor in low for anchor in anchors) or any(w in low for w in home_words + away_words):
            interesting.extend(lines[max(0, i - 1): min(len(lines), i + 6)])

    # Keep unique lines and cap the payload.
    seen: set[str] = set()
    result: list[str] = []
    for line in interesting:
        if line in seen:
            continue
        seen.add(line)
        if len(line) <= 260:
            result.append(line)
        if len(result) >= 40:
            break
    return "\n".join(result)[:7000]


async def collect_bookmaker_odds(event: Event) -> tuple[dict[str, str], list[str]]:
    """Find official Russian bookmaker event pages and return only verified pages."""
    date_text = event.start_time.strftime("%d.%m.%Y") if event.start_time else ""
    errors: list[str] = []
    found: dict[str, str] = {}

    for bookmaker, domain in BOOKMAKERS:
        query = f'site:{domain} "{event.name}" {date_text}'
        try:
            pages = await browser_search_pages(query, max_pages=3)
        except Exception as exc:
            errors.append(f"{bookmaker}: {type(exc).__name__}: {exc}")
            continue

        for url, text in pages:
            if _bookmaker_for(url) != bookmaker:
                continue
            if not _contains_match(text, event):
                continue
            compact = _odds_lines(text, event)
            if not compact:
                continue
            found[bookmaker] = f"URL: {url}\n{compact}"
            break

    return found, errors
