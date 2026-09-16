from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from bot.db import get_pool
from bot.config import ADMIN_ID
from bot.handlers.user import refresh_progress

router = Router()


@router.message(F.photo)
async def receive_screenshot(message: Message):
    """Пользователь прислал фото — ищем последнее condition_check в статусе pending для него."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            select cc.id as check_id, cc.participant_id, c.description, ct.title as contest_title
            from condition_checks cc
            join conditions c on c.id = cc.condition_id
            join participants p on p.id = cc.participant_id
            join contests ct on ct.id = p.contest_id
            where p.user_id = $1 and cc.status = 'pending' and c.type = 'manual_screenshot'
            order by cc.id desc
            limit 1
            """,
            message.from_user.id,
        )
        if not row:
            return  # не ждём от него скриншотов — игнорируем фото

        file_id = message.photo[-1].file_id
        await conn.execute(
            "update condition_checks set file_id = $1 where id = $2", file_id, row["check_id"]
        )

    await message.answer("Скриншот отправлен на проверку, жди подтверждения 🙏")

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"mod:approve:{row['check_id']}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod:reject:{row['check_id']}"),
    ]])
    await message.bot.send_photo(
        ADMIN_ID,
        photo=file_id,
        caption=f"Конкурс: {row['contest_title']}\nУсловие: {row['description']}\nОт: @{message.from_user.username or message.from_user.id}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("mod:"))
async def moderate_screenshot(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    _, action, check_id = callback.data.split(":")
    check_id = int(check_id)
    new_status = "approved" if action == "approve" else "rejected"

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """update condition_checks set status = $1, reviewed_at = now()
               where id = $2 returning participant_id""",
            new_status, check_id,
        )
        participant = await conn.fetchrow(
            "select user_id from participants where id = $1", row["participant_id"]
        )

    await callback.message.edit_caption(caption=callback.message.caption + f"\n\n→ {new_status.upper()}")
    await callback.answer()

    if new_status == "approved":
        await callback.bot.send_message(participant["user_id"], "Один из скриншотов подтверждён ✅")
        await refresh_progress(callback, row["participant_id"])
    else:
        await callback.bot.send_message(
            participant["user_id"], "Скриншот отклонён ❌ Пришли, пожалуйста, корректный."
        )
