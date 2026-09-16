from aiogram import Bot, Dispatcher
from bot.config import BOT_TOKEN
from bot.handlers import admin, user, screenshots

_bot: Bot | None = None
_dp: Dispatcher | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=BOT_TOKEN)
    return _bot


def get_dispatcher() -> Dispatcher:
    global _dp
    if _dp is None:
        _dp = Dispatcher()
        # порядок важен: у admin.py уже стоит фильтр "только ADMIN_ID" на своём роутере
        _dp.include_router(admin.router)
        _dp.include_router(screenshots.router)
        _dp.include_router(user.router)
    return _dp
