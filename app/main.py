from datetime import datetime, timedelta
from html import escape
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .analysis.ai import analyze
from .config import settings
from .sports.catalog import SPORTS
from .sports.collector_real import collect
from .sports.enrichment import enrich_event


dp = Dispatcher()
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=item.title, callback_data=f"sport:{item.key}") for item in SPORTS],
            [
                InlineKeyboardButton(text="📅 PREMATCH", callback_data="mode:prematch"),
                InlineKeyboardButton(text="🔴 LIVE", callback_data="mode:live"),
            ],
        ]
    )


def sport_menu(sport: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 PREMATCH", callback_data=f"collect:{sport}:prematch"),
                InlineKeyboardButton(text="🔴 LIVE", callback_data=f"collect:{sport}:live"),
            ],
            [InlineKeyboardButton(text="⬅️ Все виды спорта", callback_data="home")],
        ]
    )


def _now_for_event(event) -> datetime:
    if event.start_time and event.start_time.tzinfo:
        return datetime.now(event.start_time.tzinfo)
    return datetime.now(MOSCOW_TZ).replace(tzinfo=None)


def _event_visible(event, mode: str) -> bool:
    if mode == "live":
        return str(event.status or "").upper() in {"LIVE", "IN_PLAY", "IN_PROGRESS"}
    if not event.start_time:
        return False
    return event.start_time >= _now_for_event(event)


def _day_group(event, now: datetime) -> str:
    if not event.start_time:
        return "other"
    day = event.start_time.date()
    if day == now.date():
        return "today"
    if day == (now + timedelta(days=1)).date():
        return "tomorrow"
    if day == (now + timedelta(days=2)).date():
        return "day_after"
    return "other"


def event_list_menu(sport: str, mode: str, events) -> InlineKeyboardMarkup:
    rows = []
    now = datetime.now(MOSCOW_TZ)
    grouped = {"today": [], "tomorrow": [], "day_after": []}

    for index, event in enumerate(events):
        if not _event_visible(event, mode):
            continue
        group = _day_group(event, now)
        if group in grouped:
            grouped[group].append((index, event))

    labels = {
        "today": "📅 СЕГОДНЯ",
        "tomorrow": "📅 ЗАВТРА",
        "day_after": "📅 ПОСЛЕЗАВТРА",
    }
    for group in ("today", "tomorrow", "day_after"):
        items = grouped[group]
        if not items:
            continue
        rows.append([InlineKeyboardButton(text=labels[group], callback_data=f"noop:{sport}:{mode}:{group}")])
        for index, event in items:
            live = "🔴 " if event.status == "LIVE" else ""
            rows.append([
                InlineKeyboardButton(
                    text=f"{live}{event.name[:55]}",
                    callback_data=f"event:{sport}:{mode}:{index}",
                )
            ])

    rows.append([
        InlineKeyboardButton(text="🔄 Обновить список", callback_data=f"collect:{sport}:{mode}"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sport:{sport}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def event_menu(sport: str, mode: str, event, index: int) -> InlineKeyboardMarkup:
    rows = []
    if event.url:
        rows.append([InlineKeyboardButton(text="🌐 Открыть страницу матча", url=event.url)])

    research_urls = str(event.metadata.get("research_urls", "")).splitlines()
    source_buttons = []
    seen_domains = set()
    for url in research_urls:
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        if not domain or domain in seen_domains:
            continue
        seen_domains.add(domain)
        source_buttons.append(InlineKeyboardButton(text=f"🔎 {domain}", url=url))
        if len(source_buttons) == 2:
            rows.append(source_buttons)
            source_buttons = []
    if source_buttons:
        rows.append(source_buttons)

    rows.append([
        InlineKeyboardButton(text="🔄 Обновить матч", callback_data=f"event:{sport}:{mode}:{index}"),
        InlineKeyboardButton(text="⬅️ К матчам", callback_data=f"collect:{sport}:{mode}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_event_list(events, mode: str) -> list[str]:
    now = datetime.now(MOSCOW_TZ)
    grouped: dict[str, list] = {"today": [], "tomorrow": [], "day_after": []}
    for event in events:
        if not _event_visible(event, mode):
            continue
        group = _day_group(event, now)
        if group in grouped:
            grouped[group].append(event)

    labels = {
        "today": "📅 <b>СЕГОДНЯ</b>",
        "tomorrow": "📅 <b>ЗАВТРА</b>",
        "day_after": "📅 <b>ПОСЛЕЗАВТРА</b>",
    }
    lines: list[str] = []
    for group in ("today", "tomorrow", "day_after"):
        if not grouped[group]:
            continue
        lines.append(f"{labels[group]}  •  {len(grouped[group])} матч(ей)")
        for event in grouped[group]:
            time_text = event.start_time.strftime("%H:%M") if event.start_time else "время н/д"
            live = " 🔴 LIVE" if event.status == "LIVE" else ""
            lines.append(f"• {escape(event.name[:180])} — {time_text}{live}")
        lines.append("")
    return lines


def _empty_message(title: str, mode: str, errors: list[str]) -> str:
    if mode == "live":
        return (
            f"<b>{escape(title)} — LIVE</b>\n\n"
            "🔴 <b>Сейчас активных матчей не найдено.</b>\n\n"
            "Бот показывает LIVE только когда источник явно подтверждает, что матч идёт."
        )

    if errors and any("ConnectTimeout" in error or "Browser" in error for error in errors):
        return (
            f"<b>{escape(title)} — PREMATCH</b>\n\n"
            "⚠️ <b>Не удалось получить актуальный список матчей.</b>\n\n"
            "Источники временно не ответили. Нажмите «Обновить список»."
        )

    return (
        f"<b>{escape(title)} — PREMATCH</b>\n\n"
        "✅ <b>На сегодня новых матчей не осталось.</b>\n\n"
        "Все сегодняшние матчи уже начались или завершились. Если есть игры на следующие дни, они будут показаны ниже отдельными блоками."
    )


def _analysis_text(analysis: dict) -> str:
    verdict = str(analysis.get("verdict") or "SKIP").upper()
    confidence = analysis.get("confidence", 0)
    market = analysis.get("market")
    odds = analysis.get("odds")
    odds_source = analysis.get("odds_source")
    estimated = analysis.get("estimated_probability")
    implied = analysis.get("implied_probability")
    edge = analysis.get("edge")
    quality = str(analysis.get("data_quality") or "LOW").upper()
    factors = analysis.get("factors") or []
    risks = analysis.get("risks") or []
    data_gaps = analysis.get("data_gaps") or []
    summary = str(analysis.get("summary") or "").strip()

    verdict_view = {
        "BET": "🟢 <b>BET — есть подтверждённый интерес</b>",
        "WATCH": "🟡 <b>WATCH — наблюдать, но сейчас не ставить</b>",
        "SKIP": "🔴 <b>SKIP — ставку пропускаем</b>",
    }.get(verdict, "🔴 <b>SKIP — ставку пропускаем</b>")
    quality_view = {"HIGH": "🟢 HIGH", "MEDIUM": "🟡 MEDIUM", "LOW": "🔴 LOW"}.get(quality, "🔴 LOW")

    lines = [
        "<b>🤖 AI-АНАЛИЗ</b>",
        "",
        verdict_view,
        f"🎯 Уверенность в решении: <b>{escape(str(confidence))}/100</b>",
        f"📊 Качество данных: <b>{quality_view}</b>",
    ]

    if market:
        lines += ["", "<b>🎯 Рынок</b>", escape(str(market))]
        if odds:
            odds_line = f"💰 Коэффициент: <b>{escape(str(odds))}</b>"
            if odds_source:
                odds_line += f"\n   БК: {escape(str(odds_source))}"
            lines.append(odds_line)
        else:
            lines.append("⚠️ Линия не подтверждена — коэффициент не используем.")

    if estimated or implied or edge:
        lines += ["", "<b>📐 Оценка перевеса</b>"]
        if estimated:
            lines.append(f"• Оценка вероятности: {escape(str(estimated))}")
        if implied:
            lines.append(f"• Рыночная вероятность: {escape(str(implied))}")
        if edge:
            lines.append(f"• Перевес: <b>{escape(str(edge))}</b>")

    if factors:
        lines += ["", "<b>🔎 Ключевые факторы</b>"]
        lines.extend(f"• {escape(str(item))}" for item in factors[:6])

    if risks:
        lines += ["", "<b>⚠️ Риски</b>"]
        lines.extend(f"• {escape(str(item))}" for item in risks[:5])

    if data_gaps:
        lines += ["", "<b>📋 Чего не хватило</b>"]
        lines.extend(f"• {escape(str(item))}" for item in data_gaps[:6])

    if summary:
        lines += ["", "<b>📌 Итог</b>", escape(summary)]

    return "\n".join(lines)[:3800]


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "<b>AI Sports Analyst</b>\n\n"
        "🏒 КХЛ  •  🎮 Dota 2  •  🎯 CS2\n"
        "📅 PREMATCH + 🔴 LIVE\n\n"
        "Сбор событий выполняется непосредственно со страниц источников через браузер.\n"
        "Поисковые сниппеты не считаются матчами. AI не получает право придумывать матчи, счета или коэффициенты.",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "<b>AI Sports Analyst</b>\n\nВыберите вид спорта:",
        reply_markup=main_menu(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("sport:"))
async def select_sport(callback: CallbackQuery) -> None:
    sport = callback.data.split(":", 1)[1]
    title = next(item.title for item in SPORTS if item.key == sport)
    await callback.message.edit_text(
        f"<b>{escape(title)}</b>\n\nВыберите режим анализа:",
        reply_markup=sport_menu(sport),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("noop:"))
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@dp.callback_query(F.data.startswith("collect:"))
async def collect_sport(callback: CallbackQuery) -> None:
    _, sport, mode = callback.data.split(":", 2)
    title = next(item.title for item in SPORTS if item.key == sport)
    await callback.answer("Читаю страницы источников через браузер…")
    await callback.message.edit_text(f"<b>{escape(title)}</b>\n\n🔎 Browser Web Research…")

    result = await collect(sport, mode)
    visible_events = [event for event in result.events if _event_visible(event, mode)]
    if not visible_events:
        await callback.message.edit_text(
            _empty_message(title, mode, result.errors),
            reply_markup=sport_menu(sport),
        )
        return

    events = sorted(
        visible_events,
        key=lambda event: event.start_time or datetime.max,
    )
    lines = [
        f"<b>{escape(title)} — {escape(mode.upper())}</b>",
        "",
        "<b>Подтверждённые матчи</b> — только с реальных страниц источников.",
        "",
    ]
    lines.extend(_format_event_list(events, mode))
    lines.append("Нажмите на матч ниже, чтобы открыть его и запустить AI-анализ.")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=event_list_menu(sport, mode, events),
    )


@dp.callback_query(F.data.startswith("event:"))
async def select_event(callback: CallbackQuery) -> None:
    _, sport, mode, index_text = callback.data.split(":", 3)
    title = next(item.title for item in SPORTS if item.key == sport)

    await callback.answer("Собираю статистику, форму и линию российских БК…")
    await callback.message.edit_text(f"<b>{escape(title)}</b>\n\n🔎 Собираю фактические данные по матчу через браузер…")

    result = await collect(sport, mode)
    visible_events = [event for event in result.events if _event_visible(event, mode)]
    if not visible_events:
        await callback.message.edit_text(
            _empty_message(title, mode, result.errors),
            reply_markup=sport_menu(sport),
        )
        return

    events = sorted(
        visible_events,
        key=lambda event: event.start_time or datetime.max,
    )
    try:
        index = int(index_text)
    except ValueError:
        index = 0

    if index >= len(events):
        await callback.message.edit_text(
            f"<b>{escape(title)}</b>\n\nСписок матчей изменился. Нажмите «К матчам» и выберите матч заново.",
            reply_markup=sport_menu(sport),
        )
        return

    event = events[index]
    event = await enrich_event(event)
    time_text = event.start_time.strftime("%d.%m.%Y %H:%M") if event.start_time else "время н/д"
    status_text = event.status or "PREMATCH"
    score_text = f"\n🏒 Счёт: {escape(event.score)}" if event.score else ""
    pages = event.metadata.get("research_page_count", "0")
    sources = event.metadata.get("research_source_count", "0")
    bookmakers = event.metadata.get("bookmaker_names", "")
    bookmaker_count = event.metadata.get("bookmaker_count", "0")

    analysis = await analyze([event])
    analysis_block = _analysis_text(analysis)
    line_block = (
        f"📈 <b>Российские БК:</b> {escape(bookmakers)} ({escape(bookmaker_count)} подтверждено)"
        if bookmakers
        else "📈 <b>Российская линия:</b> не найдена или не подтверждена"
    )

    text = (
        f"<b>{escape(event.name)}</b>\n"
        f"🗓 {time_text}\n"
        f"📌 {escape(status_text)}{score_text}\n"
        f"{line_block}\n"
        f"🌐 Источников: <b>{escape(sources)}</b> • страниц: <b>{escape(pages)}</b>\n\n"
        f"{analysis_block}"
    )
    await callback.message.edit_text(text[:4000], reply_markup=event_menu(sport, mode, event, index))


@dp.callback_query(F.data.startswith("mode:"))
async def select_mode(callback: CallbackQuery) -> None:
    mode = callback.data.split(":", 1)[1]
    buttons = [
        [InlineKeyboardButton(text=item.title, callback_data=f"collect:{item.key}:{mode}")]
        for item in SPORTS
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    await callback.message.edit_text(
        f"<b>{escape(mode.upper())}</b>\n\nВыберите вид спорта:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


async def main() -> None:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        print("BOT STARTED", flush=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
