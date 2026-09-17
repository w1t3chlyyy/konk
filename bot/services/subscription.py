from datetime import datetime, timezone, timedelta

from bot.db import get_pool
from bot.config import ADMIN_ID, SUBSCRIPTION_DAYS


async def is_subscribed(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True  # владелец бота — всегда без ограничений

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select active, lifetime, expires_at from admin_subscription where user_id = $1",
            user_id,
        )
    if not row:
        return False
    if row["lifetime"]:
        return True
    if not row["active"]:
        return False
    return row["expires_at"] is not None and row["expires_at"] > datetime.now(timezone.utc)


async def activate_subscription(user_id: int, days: int = SUBSCRIPTION_DAYS):
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "select expires_at from admin_subscription where user_id = $1", user_id
        )
        now = datetime.now(timezone.utc)
        base = existing["expires_at"] if existing and existing["expires_at"] and existing["expires_at"] > now else now
        new_expiry = base + timedelta(days=days)

        await conn.execute(
            """
            insert into admin_subscription (user_id, active, expires_at)
            values ($1, true, $2)
            on conflict (user_id) do update set active = true, expires_at = $2, updated_at = now()
            """,
            user_id, new_expiry,
        )
