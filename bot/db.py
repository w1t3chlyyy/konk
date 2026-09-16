import asyncio
import asyncpg
from bot.config import DATABASE_URL

_pool: asyncpg.Pool | None = None
_pool_loop: asyncio.AbstractEventLoop | None = None


async def get_pool() -> asyncpg.Pool:
    """
    Аналогично Bot — пул asyncpg привязан к event loop'у, на котором создан.
    В serverless каждый вызов идёт на новом loop'е, поэтому пересоздаём пул
    при его смене (иначе см. 'Event loop is closed').
    statement_cache_size=0 нужен для работы через Supabase's pgbouncer (transaction mode).
    """
    global _pool, _pool_loop
    current_loop = asyncio.get_running_loop()

    if _pool is None or _pool_loop is not current_loop:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=3,
            statement_cache_size=0,
        )
        _pool_loop = current_loop
    return _pool
