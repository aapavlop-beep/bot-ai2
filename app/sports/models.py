from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Sport = Literal["khl", "dota2", "cs2"]
Mode = Literal["prematch", "live"]


@dataclass(slots=True)
class Event:
    sport: Sport
    mode: Mode
    name: str
    start_time: datetime | None = None
    status: str | None = None
    score: str | None = None
    source: str = "Browser Web Research"
    url: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ResearchResult:
    events: list[Event]
    source: str = "Browser Web Research"
    errors: list[str] = field(default_factory=list)
