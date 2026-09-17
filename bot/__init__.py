import asyncio
from aiogram import Bot, Dispatcher
from bot.config import BOT_TOKEN
from bot.handlers import admin, user, screenshots
from bot.services.fsm_storage import PostgresStorage

_bot: Bot | None = None
_bot_loop: asyncio.AbstractEventLoop | None = None
_dp: Dispatcher | None = None


def get_bot() -> Bot:
    """
    В serverless-среде каждый вызов asyncio.run() создаёт новый event loop.
    aiohttp-сессия внутри Bot привязывается к тому loop'у, что был активен при её
    создании — если переиспользовать Bot из предыдущего вызова, получим
    'Event loop is closed'. Поэтому пересоздаём Bot при смене loop'а.
    """
    global _bot, _bot_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _bot is None or _bot_loop is not current_loop:
        _bot = Bot(token=BOT_TOKEN)
        _bot_loop = current_loop
    return _bot


def get_dispatcher() -> Dispatcher:
    global _dp
    if _dp is None:
        _dp = Dispatcher(storage=PostgresStorage())
        # порядок важен: у admin.py уже стоит фильтр "только ADMIN_ID" на своём роутере
        _dp.include_router(admin.router)
        _dp.include_router(screenshots.router)
        _dp.include_router(user.router)
    return _dp
