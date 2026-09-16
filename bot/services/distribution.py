"""
Логика финализации конкурса: как только наступает deadline_at,
все подтверждённые (status='confirmed') участники раскладываются по местам
согласно вместимости (capacity) каждого места, в порядке подтверждения (confirmed_at).

Место с capacity = NULL — "без ограничений", туда попадают все, кто не поместился
в места с ограниченной вместимостью выше по списку (или вообще все, если это
единственное место — тот самый сценарий "все получают 1-е место").
"""

from bot.db import get_pool


async def finalize_contest(bot, contest_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            contest = await conn.fetchrow(
                "select * from contests where id = $1 and status = 'active' for update", contest_id
            )
            if not contest:
                return  # уже финализирован или не существует

            places = await conn.fetch(
                "select * from prize_places where contest_id = $1 order by place_number",
                contest_id,
            )
            participants = await conn.fetch(
                """select * from participants
                   where contest_id = $1 and status = 'confirmed'
                   order by confirmed_at asc""",
                contest_id,
            )

            assignments = {}  # user_id -> (place_number, prize_text)
            idx = 0
            for place in places:
                cap = place["capacity"]
                if cap is None:
                    # безлимитное место — забирает всех оставшихся
                    for p in participants[idx:]:
                        assignments[p["id"]] = (place["place_number"], place["prize_text"])
                    idx = len(participants)
                    break
                else:
                    for p in participants[idx: idx + cap]:
                        assignments[p["id"]] = (place["place_number"], place["prize_text"])
                    idx += cap

            for participant_id, (place_number, _) in assignments.items():
                await conn.execute(
                    "update participants set assigned_place = $1 where id = $2",
                    place_number, participant_id,
                )

            await conn.execute(
                "update contests set status = 'finished' where id = $1", contest_id
            )

    # рассылаем результаты вне транзакции
    id_to_participant = {p["id"]: p for p in participants}
    for participant_id, (place_number, prize_text) in assignments.items():
        p = id_to_participant[participant_id]
        try:
            await bot.send_message(
                p["user_id"],
                f"🏆 Конкурс «{contest['title']}» завершён!\n"
                f"Ваше место: {place_number}\nВаш приз: {prize_text}\n\n"
                f"Мы свяжемся с вами для получения приза 🎁",
            )
        except Exception:
            pass  # юзер мог заблокировать бота — не роняем весь процесс

    # тем, кто не выполнил условия до конца — тоже можно сообщить (опционально)


async def get_due_contests():
    """Конкурсы, у которых наступил дедлайн, но статус ещё active."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "select id from contests where status = 'active' and deadline_at <= now()"
        )
