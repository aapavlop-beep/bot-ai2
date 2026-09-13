from dataclasses import dataclass

from .models import Sport


@dataclass(frozen=True, slots=True)
class SportConfig:
    key: Sport
    title: str
    prematch_queries: tuple[str, ...]
    live_queries: tuple[str, ...]


SPORTS: tuple[SportConfig, ...] = (
    SportConfig(
        "khl",
        "🏒 КХЛ",
        ("КХЛ матчи сегодня расписание", "КХЛ матчи завтра расписание"),
        ("КХЛ матчи сейчас LIVE счет", "КХЛ хоккей live результаты"),
    ),
    SportConfig(
        "dota2",
        "🎮 Dota 2",
        ("Dota 2 матчи сегодня расписание", "Dota 2 upcoming matches"),
        ("Dota 2 matches live сейчас счет", "Dota 2 live matches"),
    ),
    SportConfig(
        "cs2",
        "🎯 CS2",
        ("CS2 матчи сегодня расписание", "CS2 upcoming matches"),
        ("CS2 matches live сейчас счет", "CS2 live matches"),
    ),
)

SPORT_BY_KEY = {item.key: item for item in SPORTS}
