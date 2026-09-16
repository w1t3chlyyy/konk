from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, CommandObject
from bot.db import get_pool

router = Router()


@router.message(CommandStart(deep_link=True))
async def start_with_ref(message: Message, command: CommandObject):
    payload = command.args or ""
    if not payload.startswith("c_"):
        await message.answer("Привет! Активной ссылки на конкурс не нашёл.")
        return

    ref_code = payload[2:]
    pool = await get_pool()
    async with pool.acquire() as conn:
        contest = await conn.fetchrow(
            "select * from contests where ref_code = $1 and status = 'active'", ref_code
        )
        if not contest:
            await message.answer("Этот конкурс уже недоступен.")
            return

        # создаём участника, если его ещё нет
        participant = await conn.fetchrow(
            """
            insert into participants (contest_id, user_id, username)
            values ($1, $2, $3)
            on conflict (contest_id, user_id) do nothing
            returning *
            """,
            contest["id"], message.from_user.id, message.from_user.username,
        )
        if participant is None:
            participant = await conn.fetchrow(
                "select * from participants where contest_id = $1 and user_id = $2",
                contest["id"], message.from_user.id,
            )

        conditions = await conn.fetch(
            "select * from conditions where contest_id = $1 order by sort_order", contest["id"]
        )

    await message.answer(
        f"🎉 <b>{contest['title']}</b>\n\n{contest['description']}\n\nЧтобы участвовать, выполни условия ниже:",
        parse_mode="HTML",
    )
    await send_conditions_checklist(message.bot, message.from_user.id, participant["id"], conditions)


async def send_conditions_checklist(bot: Bot, user_id: int, participant_id: int, conditions):
    pool = await get_pool()
    async with pool.acquire() as conn:
        checks = {
            row["condition_id"]: row["status"]
            for row in await conn.fetch(
                "select condition_id, status from condition_checks where participant_id = $1",
                participant_id,
            )
        }

    kb = []
    for c in conditions:
        status = checks.get(c["id"], "pending")
        mark = "✅" if status == "approved" else "⬜️"
        kb.append([InlineKeyboardButton(
            text=f"{mark} {c['description']}",
            callback_data=f"check:{participant_id}:{c['id']}",
        )])

    await bot.send_message(
        user_id,
        "Отметь выполненные условия:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )


@router.callback_query(F.data.startswith("check:"))
async def on_condition_tap(callback: CallbackQuery):
    _, participant_id, condition_id = callback.data.split(":")
    participant_id, condition_id = int(participant_id), int(condition_id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        condition = await conn.fetchrow("select * from conditions where id = $1", condition_id)

        if condition["type"] == "auto_channel_sub":
            member = await callback.bot.get_chat_member(condition["channel_id"], callback.from_user.id)
            if member.status in ("member", "administrator", "creator"):
                await conn.execute(
                    """insert into condition_checks (participant_id, condition_id, status, reviewed_at)
                       values ($1, $2, 'approved', now())
                       on conflict (participant_id, condition_id)
                       do update set status = 'approved', reviewed_at = now()""",
                    participant_id, condition_id,
                )
                await callback.answer("Подписка подтверждена ✅")
            else:
                await callback.answer("Не вижу подписки — подпишись и попробуй снова ❌", show_alert=True)
                return
        else:
            # manual_screenshot — просим прислать фото следующим сообщением
            await callback.message.answer(
                f"Пришли скриншот для условия «{condition['description']}» следующим сообщением (просто фото в чат)."
            )
            await conn.execute(
                """insert into condition_checks (participant_id, condition_id, status)
                   values ($1, $2, 'pending')
                   on conflict (participant_id, condition_id) do nothing""",
                participant_id, condition_id,
            )
            await callback.answer()
            return

    await refresh_progress(callback, participant_id)


async def refresh_progress(callback: CallbackQuery, participant_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        participant = await conn.fetchrow("select * from participants where id = $1", participant_id)
        total = await conn.fetchval(
            "select count(*) from conditions where contest_id = $1", participant["contest_id"]
        )
        approved = await conn.fetchval(
            "select count(*) from condition_checks where participant_id = $1 and status = 'approved'",
            participant_id,
        )
        if approved == total and participant["status"] != "confirmed":
            await conn.execute(
                "update participants set status = 'confirmed', confirmed_at = now() where id = $1",
                participant_id,
            )
            await callback.message.answer("Все условия выполнены — вы участвуете в конкурсе! 🎉")
