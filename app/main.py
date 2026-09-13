from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .analysis.ai import analyze
from .config import settings
from .sports.catalog import SPORTS
from .sports.collector_real import collect


dp = Dispatcher()


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


def event_list_menu(sport: str, mode: str, events) -> InlineKeyboardMarkup:
    rows = []
    for index, event in enumerate(events[:15]):
        live = "🔴 " if event.status == "LIVE" else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{live}{event.name[:55]}",
                callback_data=f"event:{sport}:{mode}:{index}",
            )
        ])
    rows.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"collect:{sport}:{mode}"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sport:{sport}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def event_menu(sport: str, mode: str, event) -> InlineKeyboardMarkup:
    rows = []
    if event.url:
        rows.append([InlineKeyboardButton(text="🌐 Открыть источник", url=event.url)])
    rows.append([
        InlineKeyboardButton(text="🔄 Обновить матч", callback_data=f"event:{sport}:{mode}:refresh"),
        InlineKeyboardButton(text="⬅️ К матчам", callback_data=f"collect:{sport}:{mode}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


@dp.callback_query(F.data.startswith("collect:"))
async def collect_sport(callback: CallbackQuery) -> None:
    _, sport, mode = callback.data.split(":", 2)
    title = next(item.title for item in SPORTS if item.key == sport)
    await callback.answer("Читаю страницы источников через браузер…")
    await callback.message.edit_text(f"<b>{escape(title)}</b>\n\n🔎 Browser Web Research…")

    result = await collect(sport, mode)
    if not result.events:
        error_text = ""
        if result.errors:
            error_text = "\n\nТехнические ошибки:\n" + "\n".join(escape(x) for x in result.errors[:3])
        await callback.message.edit_text(
            f"<b>{escape(title)}</b>\n\n"
            f"Подтверждённых матчей на реальных страницах источников не найдено.\n"
            f"Источник: Browser Web Research{error_text}",
            reply_markup=sport_menu(sport),
        )
        return

    lines = [f"<b>{escape(title)} — {escape(mode.upper())}</b>", "", "Подтверждённые матчи из источников:"]
    for event in result.events[:15]:
        time_text = event.start_time.strftime("%d.%m %H:%M") if event.start_time else "время н/д"
        live = " 🔴 LIVE" if event.status == "LIVE" else ""
        lines.append(f"• {escape(event.name[:180])} — {time_text}{live}")
    lines.append("\nНажмите на матч ниже, чтобы открыть его и запустить AI-анализ.")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=event_list_menu(sport, mode, result.events),
    )


@dp.callback_query(F.data.startswith("event:"))
async def select_event(callback: CallbackQuery) -> None:
    _, sport, mode, index_text = callback.data.split(":", 3)
    title = next(item.title for item in SPORTS if item.key == sport)

    await callback.answer("Проверяю матч и переданные данные…")
    await callback.message.edit_text(f"<b>{escape(title)}</b>\n\n🔎 Обновляю данные матча через браузер…")

    result = await collect(sport, mode)
    if not result.events:
        await callback.message.edit_text(
            f"<b>{escape(title)}</b>\n\nМатч больше не найден в источниках.\n\n"
            "Это означает, что бот не будет придумывать данные или коэффициенты.",
            reply_markup=sport_menu(sport),
        )
        return

    if index_text == "refresh":
        index = 0
    else:
        try:
            index = int(index_text)
        except ValueError:
            index = 0

    if index >= len(result.events):
        await callback.message.edit_text(
            f"<b>{escape(title)}</b>\n\nСписок матчей изменился. Нажмите «К матчам» и выберите матч заново.",
            reply_markup=sport_menu(sport),
        )
        return

    event = result.events[index]
    time_text = event.start_time.strftime("%d.%m.%Y %H:%M") if event.start_time else "время н/д"
    status_text = event.status or "PREMATCH"
    score_text = f"\n🏒 Счёт: {escape(event.score)}" if event.score else ""

    try:
        analysis = await analyze([event])
    except Exception as exc:
        analysis = f"AI-анализ временно недоступен: {type(exc).__name__}: {exc}"

    text = (
        f"<b>{escape(event.name)}</b>\n"
        f"🗓 {time_text}\n"
        f"📌 {escape(status_text)}{score_text}\n\n"
        f"<b>AI-анализ:</b>\n{escape(analysis[:3500])}"
    )
    await callback.message.edit_text(text, reply_markup=event_menu(sport, mode, event))


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
