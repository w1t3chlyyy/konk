"""
Vercel Python Function — единственный распознаваемый entrypoint (api/index.py).
POST /api  -> апдейт от Telegram (вебхук)
GET  /api  -> вызов от внешнего крона (cron-job.org), финализация просроченных конкурсов
"""
import asyncio
import json
from http.server import BaseHTTPRequestHandler

from aiogram.types import Update
from bot import get_bot, get_dispatcher
from bot.config import CRON_SECRET
from bot.services.distribution import get_due_contests, finalize_contest


async def _process_webhook(body: dict):
    bot = get_bot()
    dp = get_dispatcher()
    update = Update.model_validate(body)
    await dp.feed_update(bot, update)


async def _process_cron():
    bot = get_bot()
    due = await get_due_contests()
    for row in due:
        await finalize_contest(bot, row["id"])
    return len(due)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        body = json.loads(raw or b"{}")

        try:
            asyncio.run(_process_webhook(body))
        except Exception as e:
            # логируем, но всегда отвечаем 200 — иначе Telegram будет ретраить апдейт
            print(f"webhook error: {e}")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def do_GET(self):
        auth = self.headers.get("authorization", "")
        if CRON_SECRET and auth != f"Bearer {CRON_SECRET}":
            self.send_response(401)
            self.end_headers()
            return

        finalized_count = asyncio.run(_process_cron())

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(f'{{"finalized": {finalized_count}}}'.encode())
