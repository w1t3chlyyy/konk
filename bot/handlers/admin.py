from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from datetime import datetime
import secrets

from bot.config import ADMIN_ID, MINIAPP_URL
from bot.db import get_pool

router = Router()
router.message.filter(F.from_user.id == ADMIN_ID)  # весь этот роутер — только для админа


class NewContest(StatesGroup):
    title = State()
    description = State()
    deadline = State()
    places = State()          # ввод призов построчно: "место|приз"
    conditions = State()      # ввод условий построчно: "auto|channel_id|текст|ссылка" или "manual|-|текст|ссылка"


class EditWelcome(StatesGroup):
    text = State()
    media = State()


@router.message(Command("admin"))
async def admin_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚙️ Открыть Mini App", web_app=WebAppInfo(url=f"{MINIAPP_URL}/")),
    ]])
    await message.answer(
        "Админ-панель:\n"
        "/edit_welcome — изменить приветственный текст и медиа\n"
        "/new_contest — создать конкурс (чат, устаревающий способ)\n"
        "/contests — список конкурсов и их реф-ссылки\n\n"
        "Управление конкурсами и подписками — в Mini App:",
        reply_markup=kb,
    )


@router.message(Command("edit_welcome"))
async def edit_welcome_start(message: Message, state: FSMContext):
    await state.set_state(EditWelcome.text)
    await message.answer("Пришли новый текст приветствия (то, что видит юзер по /start).")


@router.message(StateFilter(EditWelcome.text))
async def edit_welcome_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(EditWelcome.media)
    await message.answer(
        "Теперь пришли фото или видео для приветствия — или напиши «нет», чтобы оставить без медиа."
    )


@router.message(StateFilter(EditWelcome.media), F.photo)
async def edit_welcome_photo(message: Message, state: FSMContext):
    await _save_welcome(message, state, message.photo[-1].file_id, "photo")


@router.message(StateFilter(EditWelcome.media), F.video)
async def edit_welcome_video(message: Message, state: FSMContext):
    await _save_welcome(message, state, message.video.file_id, "video")


@router.message(StateFilter(EditWelcome.media))
async def edit_welcome_skip(message: Message, state: FSMContext):
    if message.text and message.text.strip().lower() in ("нет", "no", "skip", "-"):
        await _save_welcome(message, state, None, None)
    else:
        await message.answer("Пришли фото/видео, или напиши «нет».")


async def _save_welcome(message: Message, state: FSMContext, media_id: str | None, media_type: str | None):
    data = await state.get_data()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            update bot_settings set welcome_text = $1, welcome_media_file_id = $2,
                                     welcome_media_type = $3, updated_at = now()
            where id = (select id from bot_settings order by id limit 1)
            """,
            data["text"], media_id, media_type,
        )
    await state.clear()
    await message.answer("Приветствие обновлено ✅")


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
    import re
    # нормализуем: любые разделители в дате -> "-", в времени -> ":"
    raw = message.text.strip()
    m = re.match(r"^(\d{4})\D(\d{1,2})\D(\d{1,2})\D+(\d{1,2})\D(\d{1,2})$", raw)
    if not m:
        await message.answer("Формат не понял, пример: 2026-10-01 18:00")
        return
    y, mo, d, h, mi = m.groups()
    try:
        deadline = datetime(int(y), int(mo), int(d), int(h), int(mi))
    except ValueError:
        await message.answer("Такой даты не существует, проверь и пришли ещё раз.")
        return
    await state.update_data(deadline=deadline.isoformat())
    await state.set_state(NewContest.places)
    await message.answer(
        "Призы по местам, каждый с новой строки, формат:\n"
        "<b>место|приз</b>\n\n"
        "Победители распределяются автоматически поровну между всеми местами.\n\n"
        "Например:\n1|iPhone 16\n2|Подарочный сертификат",
        parse_mode="HTML",
    )


@router.message(StateFilter(NewContest.places))
async def get_places(message: Message, state: FSMContext):
    places = []
    for line in message.text.strip().splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 2:
            await message.answer(f"Не понял строку: {line}\nПовтори ввод целиком.")
            return
        place_num, prize = parts
        try:
            place_num_int = int(place_num)
        except ValueError:
            await message.answer(f"Место должно быть числом: {line}\nПовтори ввод целиком.")
            return
        places.append({"place": place_num_int, "prize": prize})
    await state.update_data(places=places)
    await state.set_state(NewContest.conditions)
    await message.answer(
        "Условия участия, каждое с новой строки, формат:\n"
        "<b>auto|channel_id|текст|ссылка</b> — автопроверка подписки на канал\n"
        "<b>manual|-|текст|ссылка</b> — проверка по скриншоту\n"
        "(ссылка необязательна — покажется как кнопка «Выполнить»; если её нет, поставь -)\n\n"
        "Например:\nauto|-1001234567890|Подписка на канал|https://t.me/mychannel\n"
        "manual|-|Репост в сторис|-",
        parse_mode="HTML",
    )


@router.message(StateFilter(NewContest.conditions))
async def get_conditions(message: Message, state: FSMContext):
    conditions = []
    for i, line in enumerate(message.text.strip().splitlines()):
        parts = [p.strip() for p in line.split("|", 3)]
        if len(parts) != 4:
            await message.answer(f"Не понял строку: {line}\nПовтори ввод целиком.")
            return
        ctype, chan, text, link = parts
        conditions.append({
            "type": "auto_channel_sub" if ctype == "auto" else "manual_screenshot",
            "channel_id": int(chan) if ctype == "auto" else None,
            "description": text,
            "link": None if link == "-" else link,
            "sort_order": i,
        })

    data = await state.get_data()
    ref_code = secrets.token_urlsafe(6)
    deadline_dt = datetime.fromisoformat(data["deadline"])

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            contest = await conn.fetchrow(
                """insert into contests (ref_code, owner_user_id, title, description, status, deadline_at)
                   values ($1, $2, $3, $4, 'active', $5) returning id""",
                ref_code, message.from_user.id, data["title"], data["description"], deadline_dt,
            )
            for p in data["places"]:
                await conn.execute(
                    """insert into prize_places (contest_id, place_number, prize_text)
                       values ($1, $2, $3)""",
                    contest["id"], p["place"], p["prize"],
                )
            for c in conditions:
                await conn.execute(
                    """insert into conditions (contest_id, type, description, link, channel_id, sort_order)
                       values ($1, $2, $3, $4, $5, $6)""",
                    contest["id"], c["type"], c["description"], c["link"], c["channel_id"], c["sort_order"],
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
