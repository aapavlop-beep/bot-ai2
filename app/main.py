from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

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
        f"<b>{title}</b>\n\nВыберите режим анализа:",
        reply_markup=sport_menu(sport),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("collect:"))
async def collect_sport(callback: CallbackQuery) -> None:
    _, sport, mode = callback.data.split(":", 2)
    title = next(item.title for item in SPORTS if item.key == sport)
    await callback.answer("Читаю страницы источников через браузер…")
    await callback.message.edit_text(f"<b>{title}</b>\n\n🔎 Browser Web Research…")

    result = await collect(sport, mode)
    if not result.events:
        error_text = ""
        if result.errors:
            error_text = "\n\nТехнические ошибки:\n" + "\n".join(result.errors[:3])
        await callback.message.edit_text(
            f"<b>{title}</b>\n\n"
            f"Подтверждённых матчей на реальных страницах источников не найдено.\n"
            f"Источник: Browser Web Research{error_text}",
            reply_markup=sport_menu(sport),
        )
        return

    lines = [f"<b>{title} — {mode.upper()}</b>", "", "Подтверждённые матчи из источников:"]
    for event in result.events[:15]:
        time_text = event.start_time.strftime("%d.%m %H:%M") if event.start_time else "время н/д"
        live = " 🔴 LIVE" if event.status == "LIVE" else ""
        lines.append(f"• {event.name[:180]} — {time_text}{live}")
    lines.append("\nИсточник событий: Browser Web Research → реальные страницы источников.")
    await callback.message.edit_text("\n".join(lines), reply_markup=sport_menu(sport))


@dp.callback_query(F.data.startswith("mode:"))
async def select_mode(callback: CallbackQuery) -> None:
    mode = callback.data.split(":", 1)[1]
    buttons = [
        [InlineKeyboardButton(text=item.title, callback_data=f"collect:{item.key}:{mode}")]
        for item in SPORTS
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    await callback.message.edit_text(
        f"<b>{mode.upper()}</b>\n\nВыберите вид спорта:",
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
