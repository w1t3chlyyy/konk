import secrets
from datetime import datetime

from bot.db import get_pool
from bot.config import ADMIN_ID, SUBSCRIPTION_PRICE_RUB
from bot.services.telegram_auth import validate_init_data
from bot.services.subscription import is_subscribed, activate_subscription
from bot.services import cryptobot


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status


def _auth(init_data: str) -> int:
    user = validate_init_data(init_data or "")
    if user is None:
        raise ApiError("invalid init_data", 401)
    return user["id"]


async def get_contest(ref_code: str, init_data: str) -> dict:
    user_id = _auth(init_data)
    pool = await get_pool()
    async with pool.acquire() as conn:
        contest = await conn.fetchrow(
            "select * from contests where ref_code = $1 and status = 'active'", ref_code
        )
        if not contest:
            raise ApiError("contest not found", 404)

        participant = await conn.fetchrow(
            """insert into participants (contest_id, user_id)
               values ($1, $2)
               on conflict (contest_id, user_id) do nothing
               returning *""",
            contest["id"], user_id,
        )
        if participant is None:
            participant = await conn.fetchrow(
                "select * from participants where contest_id = $1 and user_id = $2",
                contest["id"], user_id,
            )

        conditions = await conn.fetch(
            "select * from conditions where contest_id = $1 order by sort_order", contest["id"]
        )
        checks = {
            r["condition_id"]: r["status"]
            for r in await conn.fetch(
                "select condition_id, status from condition_checks where participant_id = $1",
                participant["id"],
            )
        }

    return {
        "contest": {
            "title": contest["title"],
            "description": contest["description"],
            "deadline_at": contest["deadline_at"].isoformat(),
        },
        "participant_id": participant["id"],
        "status": participant["status"],
        "conditions": [
            {
                "id": c["id"],
                "type": c["type"],
                "description": c["description"],
                "link": c["link"],
                "status": checks.get(c["id"], "pending"),
            }
            for c in conditions
        ],
    }


async def check_condition(participant_id: int, condition_id: int, init_data: str, bot) -> dict:
    user_id = _auth(init_data)
    pool = await get_pool()
    async with pool.acquire() as conn:
        participant = await conn.fetchrow("select * from participants where id = $1", participant_id)
        if not participant or participant["user_id"] != user_id:
            raise ApiError("forbidden", 403)

        condition = await conn.fetchrow("select * from conditions where id = $1", condition_id)
        if not condition:
            raise ApiError("condition not found", 404)

        if condition["type"] == "auto_channel_sub":
            member = await bot.get_chat_member(condition["channel_id"], user_id)
            if member.status in ("member", "administrator", "creator"):
                await conn.execute(
                    """insert into condition_checks (participant_id, condition_id, status, reviewed_at)
                       values ($1, $2, 'approved', now())
                       on conflict (participant_id, condition_id)
                       do update set status = 'approved', reviewed_at = now()""",
                    participant_id, condition_id,
                )
                new_status = "approved"
            else:
                new_status = "not_subscribed"
        else:
            # manual_screenshot — сам скрин всё ещё шлётся в чат бота (см. screenshots.py)
            await conn.execute(
                """insert into condition_checks (participant_id, condition_id, status)
                   values ($1, $2, 'pending')
                   on conflict (participant_id, condition_id) do nothing""",
                participant_id, condition_id,
            )
            new_status = "awaiting_screenshot"

        total = await conn.fetchval(
            "select count(*) from conditions where contest_id = $1", participant["contest_id"]
        )
        approved = await conn.fetchval(
            "select count(*) from condition_checks where participant_id = $1 and status = 'approved'",
            participant_id,
        )
        confirmed = approved == total
        if confirmed and participant["status"] != "confirmed":
            await conn.execute(
                "update participants set status = 'confirmed', confirmed_at = now() where id = $1",
                participant_id,
            )

    return {"condition_status": new_status, "contest_confirmed": confirmed}


async def admin_status(init_data: str) -> dict:
    user_id = _auth(init_data)
    return {
        "is_admin": user_id == ADMIN_ID,
        "subscribed": await is_subscribed(user_id),
        "price_rub": SUBSCRIPTION_PRICE_RUB,
    }


async def create_contest(init_data: str, payload: dict, bot) -> dict:
    user_id = _auth(init_data)
    if not await is_subscribed(user_id):
        raise ApiError("subscription required", 402)

    ref_code = secrets.token_urlsafe(6)
    deadline_dt = datetime.fromisoformat(payload["deadline_at"])

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            contest = await conn.fetchrow(
                """insert into contests (ref_code, owner_user_id, title, description, status, deadline_at)
                   values ($1, $2, $3, $4, 'active', $5) returning id""",
                ref_code, user_id, payload["title"], payload.get("description", ""), deadline_dt,
            )
            for p in payload["places"]:
                await conn.execute(
                    """insert into prize_places (contest_id, place_number, prize_text)
                       values ($1, $2, $3)""",
                    contest["id"], p["place"], p["prize"],
                )
            for i, c in enumerate(payload["conditions"]):
                await conn.execute(
                    """insert into conditions (contest_id, type, description, link, channel_id, sort_order)
                       values ($1, $2, $3, $4, $5, $6)""",
                    contest["id"], c["type"], c["description"], c.get("link"), c.get("channel_id"), i,
                )

    me = await bot.get_me()
    return {"ref_link": f"https://t.me/{me.username}?start=c_{ref_code}"}


async def list_my_contests(init_data: str, bot) -> dict:
    user_id = _auth(init_data)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select * from contests where owner_user_id = $1 order by created_at desc limit 50",
            user_id,
        )
    me = await bot.get_me()
    return {
        "contests": [
            {
                "id": r["id"], "title": r["title"], "status": r["status"],
                "deadline_at": r["deadline_at"].isoformat(),
                "ref_link": f"https://t.me/{me.username}?start=c_{r['ref_code']}",
            }
            for r in rows
        ]
    }


async def create_invoice_for_subscription(init_data: str) -> dict:
    user_id = _auth(init_data)
    invoice = await cryptobot.create_invoice(
        SUBSCRIPTION_PRICE_RUB,
        payload=str(user_id),
        description="Подписка на конструктор конкурсов — 30 дней",
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """insert into payments (user_id, invoice_id, amount, asset, status)
               values ($1, $2, $3, 'RUB', 'pending')""",
            user_id, str(invoice["invoice_id"]), SUBSCRIPTION_PRICE_RUB,
        )
    return {"pay_url": invoice["pay_url"], "invoice_id": invoice["invoice_id"]}


async def check_payment(init_data: str, invoice_id: str) -> dict:
    user_id = _auth(init_data)
    status = await cryptobot.get_invoice_status(str(invoice_id))
    if status == "paid":
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("select status from payments where invoice_id = $1", str(invoice_id))
            if row and row["status"] != "paid":
                await conn.execute(
                    "update payments set status = 'paid', paid_at = now() where invoice_id = $1",
                    str(invoice_id),
                )
                await activate_subscription(user_id)
        return {"paid": True}
    return {"paid": False}
