import asyncpg
from bot.config import DATABASE_URL

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """
    В serverless-среде пул создаётся заново на каждый холодный старт функции.
    statement_cache_size=0 нужен для работы через Supabase's pgbouncer (transaction mode).
    """
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=3,
            statement_cache_size=0,
        )
    return _pool
