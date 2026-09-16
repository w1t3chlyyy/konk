import asyncio
from urllib.parse import urlparse, unquote
import asyncpg
from bot.config import DATABASE_URL

_pool: asyncpg.Pool | None = None
_pool_loop: asyncio.AbstractEventLoop | None = None


def _parse_dsn(dsn: str) -> dict:
    """
    Разбираем DSN вручную и передаём параметры по отдельности.
    Это обходит баг парсинга DSN в asyncpg, из-за которого хост-имя пулера
    иногда ошибочно пытается интерпретироваться как IP-адрес
    ('... does not appear to be an IPv4 or IPv6 address').
    """
    parsed = urlparse(dsn)
    return dict(
        user=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=(parsed.path or "/postgres").lstrip("/"),
    )


async def get_pool() -> asyncpg.Pool:
    """
    Пул asyncpg привязан к event loop'у, на котором создан. В serverless каждый
    вызов идёт на новом loop'е — пересоздаём пул при его смене, иначе
    'Event loop is closed'.
    statement_cache_size=0 нужен для работы через Supabase's pgbouncer/Supavisor
    (transaction mode). ssl='require' — Supabase pooler требует TLS.
    """
    global _pool, _pool_loop
    current_loop = asyncio.get_running_loop()

    if _pool is None or _pool_loop is not current_loop:
        conn_kwargs = _parse_dsn(DATABASE_URL)
        _pool = await asyncpg.create_pool(
            **conn_kwargs,
            ssl="require",
            min_size=1,
            max_size=3,
            statement_cache_size=0,
        )
        _pool_loop = current_loop
    return _pool
