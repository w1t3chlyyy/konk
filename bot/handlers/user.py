from aiogram import Router
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo,
)
from aiogram.filters import CommandStart, CommandObject
from bot.db import get_pool
from bot.config import MINIAPP_URL

router = Router()


@router.message(CommandStart())
async def start(message: Message, command: CommandObject):
    payload = command.args or ""
    ref_code = payload[2:] if payload.startswith("c_") else None

    pool = await get_pool()
    async with pool.acquire() as conn:
        settings = await conn.fetchrow("select * from bot_settings order by id limit 1")

    miniapp_url = f"{MINIAPP_URL}/"
    if ref_code:
        miniapp_url += f"?ref={ref_code}"

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎉 Открыть", web_app=WebAppInfo(url=miniapp_url)),
    ]])

    text = settings["welcome_text"] if settings else "Добро пожаловать! 🎉"
    media_id = settings["welcome_media_file_id"] if settings else None
    media_type = settings["welcome_media_type"] if settings else None

    if media_id and media_type == "photo":
        await message.answer_photo(media_id, caption=text, reply_markup=kb)
    elif media_id and media_type == "video":
        await message.answer_video(media_id, caption=text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)
