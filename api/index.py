"""
Vercel Python Function — единственный распознаваемый entrypoint (api/index.py).
Всё маршрутизируется через query-параметр ?action=...:

  POST /api?action=telegram_webhook   -> апдейты от Telegram (сюда указывает setWebhook)
  GET  /api?action=cron                -> вызов от внешнего крона (финализация конкурсов)
  GET  /api?action=contest&ref=...     -> данные конкурса для Mini App
  POST /api?action=check_condition     -> отметить условие выполненным
  GET  /api?action=admin_status        -> is_admin / subscribed для Mini App
  POST /api?action=create_contest      -> создать конкурс (админ, нужна подписка)
  GET  /api?action=list_contests       -> список конкурсов (админ)
  POST /api?action=create_invoice      -> создать инвойс CryptoBot на подписку
  POST /api?action=check_payment       -> проверить, оплачен ли инвойс
"""
import asyncio
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from aiogram.types import Update
from bot import get_bot, get_dispatcher
from bot.config import CRON_SECRET
from bot.services.distribution import get_due_contests, finalize_contest
from bot.services import miniapp_api
from bot.services.miniapp_api import ApiError


async def _telegram_webhook(body: dict):
    bot = get_bot()
    dp = get_dispatcher()
    update = Update.model_validate(body)
    await dp.feed_update(bot, update)


async def _cron():
    bot = get_bot()
    due = await get_due_contests()
    for row in due:
        await finalize_contest(bot, row["id"])
    return {"finalized": len(due)}


async def _dispatch_get(action: str, params: dict) -> dict:
    if action == "contest":
        return await miniapp_api.get_contest(params["ref"][0], params.get("init_data", [""])[0])
    if action == "admin_status":
        return await miniapp_api.admin_status(params.get("init_data", [""])[0])
    if action == "list_contests":
        return await miniapp_api.list_my_contests(params.get("init_data", [""])[0], get_bot())
    raise ApiError(f"unknown action: {action}", 404)


async def _dispatch_post(action: str, body: dict) -> dict:
    init_data = body.get("init_data", "")
    if action == "check_condition":
        return await miniapp_api.check_condition(
            body["participant_id"], body["condition_id"], init_data, get_bot()
        )
    if action == "create_contest":
        return await miniapp_api.create_contest(init_data, body, get_bot())
    if action == "create_invoice":
        return await miniapp_api.create_invoice_for_subscription(init_data)
    if action == "check_payment":
        return await miniapp_api.check_payment(init_data, body["invoice_id"])
    raise ApiError(f"unknown action: {action}", 404)


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        action = params.get("action", [None])[0]

        if action == "cron":
            auth = self.headers.get("authorization", "")
            if CRON_SECRET and auth != f"Bearer {CRON_SECRET}":
                self._send_json(401, {"error": "unauthorized"})
                return
            result = asyncio.run(_cron())
            self._send_json(200, result)
            return

        if not action:
            self._send_json(400, {"error": "missing action"})
            return

        try:
            result = asyncio.run(_dispatch_get(action, params))
            self._send_json(200, result)
        except ApiError as e:
            self._send_json(e.status, {"error": e.message})
        except Exception as e:
            print(f"GET {action} error: {e}")
            self._send_json(500, {"error": "internal error"})

    def do_POST(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        action = params.get("action", [None])[0]

        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        body = json.loads(raw or b"{}")

        if action == "telegram_webhook":
            try:
                asyncio.run(_telegram_webhook(body))
            except Exception as e:
                print(f"webhook error: {e}")
            self._send_json(200, {"ok": True})
            return

        if not action:
            self._send_json(400, {"error": "missing action"})
            return

        try:
            result = asyncio.run(_dispatch_post(action, body))
            self._send_json(200, result)
        except ApiError as e:
            self._send_json(e.status, {"error": e.message})
        except Exception as e:
            print(f"POST {action} error: {e}")
            self._send_json(500, {"error": "internal error"})
