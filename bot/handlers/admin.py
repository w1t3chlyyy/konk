from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from datetime import datetime
import secrets

from bot.config import ADMIN_ID
from bot.db import get_pool

router = Router()
router.message.filter(F.from_user.id == ADMIN_ID)  # весь этот роутер — только для админа


class NewContest(StatesGroup):
    title = State()
    description = State()
    deadline = State()
    places = State()          # ввод призов построчно: "1|Приз|Вместимость" (вместимость - или число, или *)
    conditions = State()      # ввод условий построчно: "auto|channel_id|текст" или "manual|-|текст"


@router.message(Command("admin"))
async def admin_menu(message: Message):
    await message.answer(
        "Админ-панель:\n"
        "/new_contest — создать конкурс\n"
        "/contests — список конкурсов и их реф-ссылки"
    )


@router.message(Command("new_contest"))
async def new_contest_start(message: Message, state: FSMContext):
    await state.set_state(NewContest.title)
    await message.answer("Название конкурса?")


@router.message(StateFilter(NewContest.title))
async def get_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(NewContest.description)
    await message.answer("Описание конкурса (что участникам покажем)?")


@router.message(StateFilter(NewContest.description))
async def get_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(NewContest.deadline)
    await message.answer("Дедлайн в формате ГГГГ-ММ-ДД ЧЧ:ММ (по UTC)?")


@router.message(StateFilter(NewContest.deadline))
async def get_deadline(message: Message, state: FSMContext):
    try:
        deadline = datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer("Формат не понял, пример: 2026-10-01 18:00")
        return
    await state.update_data(deadline=deadline.isoformat())
    await state.set_state(NewContest.places)
    await message.answer(
        "Призы по местам, каждый с новой строки, формат:\n"
        "<b>место|приз|вместимость</b> (вместимость — число или * для «без ограничений»)\n\n"
        "Например:\n1|iPhone 16|1\n2|Подарочный сертификат|*",
        parse_mode="HTML",
    )


@router.message(StateFilter(NewContest.places))
async def get_places(message: Message, state: FSMContext):
    places = []
    for line in message.text.strip().splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3:
            await message.answer(f"Не понял строку: {line}\nПовтори ввод целиком.")
            return
        place_num, prize, cap = parts
        places.append({
            "place": int(place_num),
            "prize": prize,
            "capacity": None if cap == "*" else int(cap),
        })
    await state.update_data(places=places)
    await state.set_state(NewContest.conditions)
    await message.answer(
        "Условия участия, каждое с новой строки, формат:\n"
        "<b>auto|channel_id|текст</b> — автопроверка подписки на канал\n"
        "<b>manual|-|текст</b> — проверка по скриншоту\n\n"
        "Например:\nauto|-1001234567890|Подписка на канал\nmanual|-|Репост в сторис",
        parse_mode="HTML",
    )


@router.message(StateFilter(NewContest.conditions))
async def get_conditions(message: Message, state: FSMContext):
    conditions = []
    for i, line in enumerate(message.text.strip().splitlines()):
        parts = [p.strip() for p in line.split("|", 2)]
        if len(parts) != 3:
            await message.answer(f"Не понял строку: {line}\nПовтори ввод целиком.")
            return
        ctype, chan, text = parts
        conditions.append({
            "type": "auto_channel_sub" if ctype == "auto" else "manual_screenshot",
            "channel_id": int(chan) if ctype == "auto" else None,
            "description": text,
            "sort_order": i,
        })

    data = await state.get_data()
    ref_code = secrets.token_urlsafe(6)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            contest = await conn.fetchrow(
                """insert into contests (ref_code, title, description, status, deadline_at)
                   values ($1, $2, $3, 'active', $4) returning id""",
                ref_code, data["title"], data["description"], data["deadline"],
            )
            for p in data["places"]:
                await conn.execute(
                    """insert into prize_places (contest_id, place_number, prize_text, capacity)
                       values ($1, $2, $3, $4)""",
                    contest["id"], p["place"], p["prize"], p["capacity"],
                )
            for c in conditions:
                await conn.execute(
                    """insert into conditions (contest_id, type, description, channel_id, sort_order)
                       values ($1, $2, $3, $4, $5)""",
                    contest["id"], c["type"], c["description"], c["channel_id"], c["sort_order"],
                )

    me = await message.bot.get_me()
    await state.clear()
    await message.answer(
        f"Конкурс создан ✅\nРеф-ссылка:\nhttps://t.me/{me.username}?start=c_{ref_code}"
    )


@router.message(Command("contests"))
async def list_contests(message: Message):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("select * from contests order by created_at desc limit 20")
    me = await message.bot.get_me()
    if not rows:
        await message.answer("Конкурсов пока нет.")
        return
    lines = [
        f"• {r['title']} [{r['status']}] — t.me/{me.username}?start=c_{r['ref_code']}"
        for r in rows
    ]
    await message.answer("\n".join(lines))
