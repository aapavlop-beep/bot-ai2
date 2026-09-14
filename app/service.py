"""Search and monitoring application layer, independent of Telegram rendering."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from .analysis.ai import analyze, build_options
from .config import settings
from .sports.collector_real import collect
from .sports.enrichment import enrich_event
from .sports.models import Event, ResearchResult
from .storage import Store

logger = logging.getLogger(__name__)
Notify = Callable[[int, int, list[dict], str], Awaitable[None]]
LIVE = {"LIVE", "IN_PLAY", "IN_PROGRESS"}
FINISHED = {"FINISHED", "FINAL", "FT", "CANCELLED", "POSTPONED", "ENDED"}


def visible(event: Event, mode: str) -> bool:
    if str(event.status or "").upper() in FINISHED:
        return False
    if mode == "live":
        return str(event.status or "").upper() in LIVE
    if str(event.status or "").upper() in LIVE or not event.start_time:
        return False
    start = event.start_time
    if start.tzinfo is None:
        start = start.replace(tzinfo=ZoneInfo("Europe/Moscow"))
    return start >= datetime.now(timezone.utc)


def material_change(old: dict | None, new: dict) -> bool:
    if old is None:
        return True
    for field_name in ("verdict", "risk", "market", "odds_source", "data_quality"):
        if old.get(field_name) != new.get(field_name):
            return True
    for field_name, tolerance in (("odds", .03), ("estimated_probability", .03)):
        before, after = old.get(field_name), new.get(field_name)
        if (before is None) != (after is None):
            return True
        if before is not None and after is not None:
            delta = abs(float(after) - float(before))
            if field_name == "odds":
                delta /= max(1.0, float(before))
            if delta >= tolerance:
                return True
    return False


@dataclass
class Search:
    user_id: int
    chat_id: int
    sports: list[str]
    mode: str
    event_id: str | None = None
    token: str = field(default_factory=lambda: uuid4().hex)
    notified: dict[str, dict] = field(default_factory=dict)
    cursor: int = 0
    missing: int = 0


class SearchService:
    def __init__(self, store: Store, notify: Notify):
        self.store = store
        self.notify = notify
        self.tasks: dict[int, asyncio.Task] = {}
        self.searches: dict[int, Search] = {}
        self.semaphore = asyncio.Semaphore(settings.analysis_concurrency)

    def is_running(self, user_id: int) -> bool:
        task = self.tasks.get(user_id)
        return bool(task and not task.done())

    async def list_events(self, user_id: int, sport: str, mode: str) -> ResearchResult:
        if sport not in {"khl", "dota2", "cs2"} or mode not in {"prematch", "live"}:
            raise ValueError("Неизвестный вид спорта или режим.")
        result = await collect(sport, mode)
        events = sorted((event for event in result.events if visible(event, mode)), key=lambda event: (event.start_time.isoformat() if event.start_time else "", event.name))
        for event in events:
            payload = asdict(event)
            payload["start_time"] = event.start_time.isoformat() if event.start_time else None
            self.store.save_event(user_id, event.id, payload)
        return ResearchResult(events=events, source=result.source, errors=result.errors)

    async def start(self, user_id: int, chat_id: int, sports: list[str], mode: str, event_id: str | None = None) -> None:
        if not sports or not set(sports) <= {"khl", "dota2", "cs2"} or mode not in {"prematch", "live"}:
            raise ValueError("Неизвестный вид спорта или режим.")
        bank = self.store.get_bank(user_id)
        if bank is None or bank["available"] <= 0:
            raise ValueError("Сначала укажите доступный банк на сегодня.")
        if event_id:
            saved = self.store.get_event(user_id, event_id)
            if not saved or saved["sport"] not in sports or saved["mode"] != mode:
                raise ValueError("Список матчей устарел. Выберите матч заново.")
        await self.stop(user_id)
        search = Search(user_id, chat_id, list(dict.fromkeys(sports)), mode, event_id)
        self.store.save_search(user_id, chat_id, search.sports, mode, event_id)
        self.searches[user_id] = search
        self.tasks[user_id] = asyncio.create_task(self._run(search), name=f"sports-search-{user_id}")

    async def stop(self, user_id: int) -> None:
        self.searches.pop(user_id, None)
        task = self.tasks.pop(user_id, None)
        self.store.stop_search(user_id)
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _active(self, search: Search) -> bool:
        return self.searches.get(search.user_id) is search

    async def _send(self, search: Search, items: list[dict], message: str) -> None:
        if self._active(search):
            await self.notify(search.user_id, search.chat_id, items, message)

    async def _analyze(self, event: Event) -> tuple[Event, dict] | None:
        async with self.semaphore:
            try:
                # A timeout includes browser research and model work. Each source
                # also has its own shorter timeout; unrelated matches keep running.
                deadline = 70 if event.mode == "live" else 240
                async with asyncio.timeout(deadline):
                    enriched = await enrich_event(event)
                    return enriched, await analyze([enriched])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Analysis failed: %s", event.id)
                return None

    async def _publish(self, search: Search, completed: list[tuple[Event, dict]], first: bool,
                       errors: list, total: int) -> bool:
        bank = self.store.get_bank(search.user_id)
        if not bank or not self._active(search):
            return False
        options = build_options(completed, max(0, bank["available"]), limit=100)
        changed = False
        for item in options[:5]:
            key = item.get("id") or f"{item.get('event_id')}:{item.get('market')}:{item.get('odds_source')}"
            if material_change(search.notified.get(key), item):
                changed = True
        if not options or (not first and not changed):
            return False
        saved = self.store.save_recommendations(search.user_id, uuid4().hex, options)
        headline = "🏆 Лучшие варианты по результатам анализа" if first else "🔴 Новые варианты или существенные изменения"
        if len(completed) < total:
            headline += f"\nРазобрано матчей: {len(completed)} из {total}. Остальные ещё проверяются."
        if errors:
            headline += "\nЧасть источников недоступна — охват неполный."
        if search.mode == "live":
            headline += "\nНаблюдение продолжается до «Поставил выбранные» или остановки."
        await self._send(search, saved[:5], headline)
        for item in options[:5]:
            key = item.get("id") or f"{item.get('event_id')}:{item.get('market')}:{item.get('odds_source')}"
            search.notified[key] = item
        return True

    async def _run(self, search: Search) -> None:
        first = True
        failed = False
        try:
            while self._active(search):
                bank = self.store.get_bank(search.user_id)
                if not bank or bank["available"] <= 0:
                    await self._send(search, [], "Укажите доступный банк на сегодня и запустите поиск заново.")
                    break
                if bank["loss_limit"] and bank["pnl"] <= -bank["loss_limit"]:
                    await self._send(search, [], "Достигнут выбранный дневной предел потерь. Поиск остановлен.")
                    break
                responses = await asyncio.gather(*(self.list_events(search.user_id, sport, search.mode) for sport in search.sports), return_exceptions=True)
                events: dict[str, Event] = {}
                errors = []
                for response in responses:
                    if isinstance(response, BaseException):
                        errors.append(type(response).__name__)
                    else:
                        errors.extend(response.errors)
                        events.update({event.id: event for event in response.events})
                if search.event_id:
                    events = {key: value for key, value in events.items() if key == search.event_id}
                candidates = list(events.values())
                if not candidates:
                    search.missing += 1
                    if first or not failed:
                        text = "Не удалось подтвердить актуальные матчи: источники не ответили или не дали подходящих данных." if errors else "Подходящие матчи сейчас не найдены."
                        if search.mode == "live":
                            text += " Наблюдение продолжается; сообщу, когда появится вариант."
                        await self._send(search, [], text)
                    failed = True
                    # For a specific match, disappearances do not mean final
                    # score: only end after repeated successful empty collections.
                    if search.event_id and search.missing >= 3 and not errors:
                        await self._send(search, [], "Матч больше не подтверждается как LIVE. Наблюдение завершено.")
                        break
                else:
                    search.missing = 0
                    failed = False
                    total = len(candidates)
                    if search.mode == "live":
                        # Fair rotation; avoid scanning only the first matches in
                        # a busy schedule. Only fresh results enter each ranking.
                        size = min(len(candidates), settings.live_batch_size)
                        start = search.cursor % len(candidates)
                        candidates = (candidates[start:] + candidates[:start])[:size]
                        search.cursor += size
                    workers = [asyncio.create_task(self._analyze(event)) for event in candidates]
                    completed = []
                    published = False
                    try:
                        for worker in asyncio.as_completed(workers):
                            result = await worker
                            if result is not None:
                                completed.append(result)
                            if search.mode == "live" and result is not None:
                                sent = await self._publish(search, completed, first and not published, errors, total)
                                published = published or sent
                        if search.mode != "live":
                            published = await self._publish(search, completed, first, errors, total)
                    finally:
                        # Cancelling a search also cancels workers created by
                        # as_completed; no orphan model calls or browser pages.
                        for worker in workers:
                            if not worker.done():
                                worker.cancel()
                        await asyncio.gather(*workers, return_exceptions=True)
                    if first and not published:
                        await self._send(search, [], "Анализ не дал обоснованных вариантов. Данных недостаточно; выдумывать исходы бот не будет." + (" Наблюдение продолжается." if search.mode == "live" else ""))
                first = False
                if search.mode != "live":
                    break
                await asyncio.sleep(settings.monitoring_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Search failed for user %s", search.user_id)
            try:
                await self._send(search, [], "Поиск прерван из-за ошибки. Запустите его снова кнопкой поиска.")
            except Exception:
                logger.exception("Could not notify user about search failure")
        finally:
            if self._active(search):
                self.searches.pop(search.user_id, None)
                self.tasks.pop(search.user_id, None)
                self.store.stop_search(search.user_id)

    async def restore(self) -> None:
        import json
        for row in self.store.interrupt_searches():
            if row["mode"] != "live":
                continue
            try:
                await self.start(row["user_id"], row["chat_id"], json.loads(row["sports"]), row["mode"], row["event_id"])
            except ValueError:
                logger.info("Search needs a fresh daily bank or match selection for user %s", row["user_id"])

    async def close(self) -> None:
        # Preserve persisted subscriptions on orderly shutdown for restore().
        tasks = list(self.tasks.values())
        self.searches.clear()
        self.tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
