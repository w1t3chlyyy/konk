from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from bot.db import get_pool

router = Router()


@router.message(F.photo)
async def receive_screenshot(message: Message):
    """Пользователь прислал фото — ищем последний pending condition_check для него."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            select cc.id as check_id, cc.participant_id, c.description,
                   ct.title as contest_title, ct.owner_user_id
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
    # уходит владельцу КОНКРЕТНОГО конкурса (в мультипользовательской модели это не всегда владелец бота)
    await message.bot.send_photo(
        row["owner_user_id"],
        photo=file_id,
        caption=f"Конкурс: {row['contest_title']}\nУсловие: {row['description']}\nОт: @{message.from_user.username or message.from_user.id}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("mod:"))
async def moderate_screenshot(callback: CallbackQuery):
    _, action, check_id = callback.data.split(":")
    check_id = int(check_id)
    new_status = "approved" if action == "approve" else "rejected"

    pool = await get_pool()
    async with pool.acquire() as conn:
        owner = await conn.fetchval(
            """
            select ct.owner_user_id
            from condition_checks cc
            join participants p on p.id = cc.participant_id
            join contests ct on ct.id = p.contest_id
            where cc.id = $1
            """,
            check_id,
        )
        if owner != callback.from_user.id:
            await callback.answer("Это не твой конкурс.", show_alert=True)
            return

        row = await conn.fetchrow(
            """update condition_checks set status = $1, reviewed_at = now()
               where id = $2 returning participant_id""",
            new_status, check_id,
        )
        participant = await conn.fetchrow(
            "select * from participants where id = $1", row["participant_id"]
        )

        confirmed = False
        if new_status == "approved":
            total = await conn.fetchval(
                "select count(*) from conditions where contest_id = $1", participant["contest_id"]
            )
            approved = await conn.fetchval(
                "select count(*) from condition_checks where participant_id = $1 and status = 'approved'",
                row["participant_id"],
            )
            confirmed = approved == total
            if confirmed and participant["status"] != "confirmed":
                await conn.execute(
                    "update participants set status = 'confirmed', confirmed_at = now() where id = $1",
                    row["participant_id"],
                )

    await callback.message.edit_caption(caption=callback.message.caption + f"\n\n→ {new_status.upper()}")
    await callback.answer()

    if new_status == "approved":
        text = "Один из скриншотов подтверждён ✅"
        if confirmed:
            text += "\nВсе условия выполнены — вы участвуете в конкурсе! 🎉"
        await callback.bot.send_message(participant["user_id"], text)
    else:
        await callback.bot.send_message(
            participant["user_id"], "Скриншот отклонён ❌ Пришли, пожалуйста, корректный."
        )
