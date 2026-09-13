from dataclasses import dataclass

from .models import Sport


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    url: str
    sport: Sport
    supports_prematch: bool = True
    supports_live: bool = True


SOURCES = (
    Source("Sports.ru КХЛ", "https://www.sports.ru/hockey/tournament/khl/calendar/", "khl", True, False),
    Source("Чемпионат КХЛ", "https://www.championat.com/hockey/_superleague/tournament/7092/calendar/", "khl", True, False),
    Source("Sports.ru Хоккей LIVE", "https://www.sports.ru/hockey/match/", "khl", False, True),
    Source("HLTV Matches", "https://www.hltv.org/matches", "cs2", True, True),
    Source("Liquipedia Dota 2", "https://liquipedia.net/dota2/Matches", "dota2", True, True),
)


def sources_for(sport: Sport, mode: str | None = None) -> tuple[Source, ...]:
    sources = tuple(source for source in SOURCES if source.sport == sport)
    if mode == "prematch":
        return tuple(source for source in sources if source.supports_prematch)
    if mode == "live":
        return tuple(source for source in sources if source.supports_live)
    return sources
