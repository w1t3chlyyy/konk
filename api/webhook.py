"""
Vercel Python Function. Telegram шлёт сюда POST при каждом апдейте.
URL вебхука нужно один раз зарегистрировать (см. README, шаг "Установка вебхука").
"""
import asyncio
import json
from http.server import BaseHTTPRequestHandler

from aiogram.types import Update
from bot import get_bot, get_dispatcher


async def _process(body: dict):
    bot = get_bot()
    dp = get_dispatcher()
    update = Update.model_validate(body)
    await dp.feed_update(bot, update)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        body = json.loads(raw or b"{}")

        try:
            asyncio.run(_process(body))
        except Exception as e:
            # логируем, но всегда отвечаем 200 — иначе Telegram будет ретраить апдейт
            print(f"webhook error: {e}")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')
