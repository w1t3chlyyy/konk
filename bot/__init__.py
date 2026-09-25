from bot.config import BOT_TOKEN, ADMIN_ID, MINIAPP_URL

def get_bot():
    try:
        from aiogram import Bot
        if BOT_TOKEN:
            return Bot(token=BOT_TOKEN)
    except Exception:
        pass
    return None

def get_dispatcher():
    try:
        from aiogram import Dispatcher
        return Dispatcher()
    except Exception:
        pass
    return None

__all__ = ["get_bot", "get_dispatcher", "BOT_TOKEN", "ADMIN_ID", "MINIAPP_URL"]
