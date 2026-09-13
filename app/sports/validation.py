from __future__ import annotations

from .models import Event


def is_confirmed(event: Event) -> bool:
    """Reject search noise until a source parser supplies event facts."""
    if not event.name.strip() or not event.url:
        return False
    if event.mode == "live":
        # A LIVE event must have an explicit live status or score from a source.
        return bool(event.status or event.score)
    return True


def confirmed_events(events: list[Event]) -> list[Event]:
    return [event for event in events if is_confirmed(event)]
