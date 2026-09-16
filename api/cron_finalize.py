"""
Vercel Cron Job дёргает этот эндпоинт по расписанию (см. vercel.json — раз в 15 минут).
Проверяем, не наступил ли дедлайн у активных конкурсов, и если да — распределяем места.
"""
import asyncio
from http.server import BaseHTTPRequestHandler

from bot import get_bot
from bot.config import CRON_SECRET
from bot.services.distribution import get_due_contests, finalize_contest


async def _run():
    bot = get_bot()
    due = await get_due_contests()
    for row in due:
        await finalize_contest(bot, row["id"])
    return len(due)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Vercel Cron добавляет заголовок Authorization: Bearer <CRON_SECRET> если он задан в настройках проекта
        auth = self.headers.get("authorization", "")
        if CRON_SECRET and auth != f"Bearer {CRON_SECRET}":
            self.send_response(401)
            self.end_headers()
            return

        finalized_count = asyncio.run(_run())

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(f'{{"finalized": {finalized_count}}}'.encode())
