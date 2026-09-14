from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import re
import unicodedata
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

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

    @property
    def id(self) -> str:
        """Identity survives reordered lists, changed times, modes and scores.

        Legacy naive timestamps are Moscow time. Unknown dates must not be
        replaced with today's date each time id is read; collectors record an
        observation date or a reliable match URL and reject generic search hits.
        """
        name = unicodedata.normalize("NFKC", self.name).casefold().replace("ё", "е")
        name = re.sub(r"\s+(?:[—–-]|vs\.?|versus)\s+", "|", name)
        name = re.sub(r"\s+", " ", name).strip()
        day = self.metadata.get("event_date", "")
        if self.start_time:
            value = self.start_time
            if value.tzinfo:
                value = value.astimezone(ZoneInfo("Europe/Moscow"))
            day = value.date().isoformat()
        fallback = ""
        if not day and self.metadata.get("source_event_url"):
            url = urlsplit(self.metadata["source_event_url"])
            fallback = urlunsplit((url.scheme, url.netloc.lower(), url.path.rstrip("/"), "", ""))
        return sha256(f"{self.sport}|{name}|{day or fallback or 'undated'}".encode()).hexdigest()[:16]


@dataclass(slots=True)
class ResearchResult:
    events: list[Event]
    source: str = "Browser Web Research"
    errors: list[str] = field(default_factory=list)
