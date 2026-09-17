"""
Aiogram по умолчанию хранит состояние диалогов (FSM) в памяти процесса (MemoryStorage).
В serverless (Vercel) каждый запрос может попасть в новый процесс — память не разделяется
между вызовами, и многошаговые диалоги (/edit_welcome, /new_contest) иногда "теряют" часть
данных. Здесь то же самое хранилище, но поверх Supabase — переживает холодные старты.
"""
import json
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from bot.db import get_pool


def _key_str(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}"


class PostgresStorage(BaseStorage):
    async def set_state(self, key: StorageKey, state=None):
        state_str = state.state if hasattr(state, "state") else state
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                insert into fsm_state (key, state) values ($1, $2)
                on conflict (key) do update set state = $2, updated_at = now()
                """,
                _key_str(key), state_str,
            )

    async def get_state(self, key: StorageKey):
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("select state from fsm_state where key = $1", _key_str(key))
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: dict):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                insert into fsm_state (key, data) values ($1, $2::jsonb)
                on conflict (key) do update set data = $2::jsonb, updated_at = now()
                """,
                _key_str(key), json.dumps(data),
            )

    async def get_data(self, key: StorageKey) -> dict:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("select data from fsm_state where key = $1", _key_str(key))
        if not row or not row["data"]:
            return {}
        raw = row["data"]
        return json.loads(raw) if isinstance(raw, str) else raw

    async def close(self):
        pass
