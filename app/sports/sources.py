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
    Source("Sports.ru КХЛ", "https://www.sports.ru/hockey/tournament/khl/calendar/", "khl"),
    Source("HLTV Matches", "https://www.hltv.org/matches", "cs2"),
    Source("Liquipedia Dota 2", "https://liquipedia.net/dota2/Matches", "dota2"),
)


def sources_for(sport: Sport) -> tuple[Source, ...]:
    return tuple(source for source in SOURCES if source.sport == sport)
