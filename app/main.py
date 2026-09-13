from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message

from .config import settings


dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "🏒 <b>КХЛ AI Analyst</b>\n\n"
        "Чистая локальная версия проекта запущена.\n"
        "Следующим этапом подключаем браузерный сбор матчей и статистики.",
    )


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
