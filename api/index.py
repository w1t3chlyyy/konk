import json
import os
import secrets
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

ADMIN_ID = int(os.environ.get("ADMIN_ID", "123456789"))
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
vercel_url = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or os.environ.get("VERCEL_URL")
default_miniapp_url = f"https://{vercel_url}" if vercel_url else "http://localhost:3000"
MINIAPP_URL = os.environ.get("MINIAPP_URL") or default_miniapp_url
CRYPTOBOT_TOKEN = os.environ.get("CRYPTOBOT_TOKEN", "")
SUBSCRIPTION_PRICE_RUB = 390

def send_telegram_api(method: str, payload: dict):
    token = os.environ.get("BOT_TOKEN") or BOT_TOKEN
    if not token:
        print("[Telegram API] Warning: BOT_TOKEN is not configured")
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "ContestBot/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            res_bytes = resp.read()
            return json.loads(res_bytes.decode("utf-8"))
    except Exception as e:
        print(f"[Telegram API] Error in {method}: {e}")
        return None

# In-memory stores
contests = {
    1: {
        "id": 1,
        "ref_code": "demo",
        "owner_user_id": ADMIN_ID,
        "title": "Розыгрыш Telegram Premium и iPhone 16",
        "description": "Выполните условия ниже, чтобы получить подтверждение участия и побороться за топовые призы!",
        "status": "active",
        "deadline_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
}

prize_places = {
    1: {"id": 1, "contest_id": 1, "place_number": 1, "prize_text": "iPhone 16 Pro 256GB", "capacity": 1},
    2: {"id": 2, "contest_id": 1, "place_number": 2, "prize_text": "Telegram Premium на 1 год", "capacity": 3},
    3: {"id": 3, "contest_id": 1, "place_number": 3, "prize_text": "Telegram Stars (1 000 ⭐)", "capacity": None},
}

conditions = {
    1: {
        "id": 1,
        "contest_id": 1,
        "type": "auto_channel_sub",
        "description": "Подписаться на новостной Telegram-канал",
        "channel_id": -1001234567890,
        "link": "https://t.me/telegram",
        "sort_order": 0,
    },
    2: {
        "id": 2,
        "contest_id": 1,
        "type": "manual_screenshot",
        "description": "Отправить скриншот репоста анонса",
        "link": "https://t.me/durov",
        "sort_order": 1,
    },
}

participants = {}
condition_checks = {}
subscriptions = {ADMIN_ID: {"active": True, "lifetime": True, "expires_at": None}}
payments = {}

next_contest_id = 2
next_condition_id = 3
next_participant_id = 1
next_check_id = 1
next_payment_id = 1


def get_user_id(init_data: str) -> int:
    if not init_data:
        return ADMIN_ID
    try:
        qs = parse_qs(init_data)
        user_str = qs.get("user", [None])[0]
        if user_str:
            data = json.loads(user_str)
            return data.get("id", ADMIN_ID)
    except Exception:
        pass
    return ADMIN_ID


def process_api_request(method: str, query_string: str, body_bytes: bytes, headers: dict) -> tuple[int, dict]:
    global next_participant_id, next_contest_id, next_condition_id, next_check_id, next_payment_id

    params = parse_qs(query_string)
    action = params.get("action", [None])[0]
    init_data = params.get("init_data", [""])[0]

    body = {}
    if body_bytes:
        try:
            body = json.loads(body_bytes.decode("utf-8"))
            if not action:
                action = body.get("action")
            if not init_data:
                init_data = body.get("init_data", "")
        except Exception:
            pass

    if method == "OPTIONS":
        return 204, {}

    if method == "GET":
        if action == "cron":
            auth = headers.get("authorization", "")
            if CRON_SECRET and auth != f"Bearer {CRON_SECRET}":
                return 401, {"error": "unauthorized"}
            return 200, {"finalized": 0}

        if action == "admin_status":
            uid = get_user_id(init_data)
            return 200, {
                "is_admin": uid == ADMIN_ID,
                "subscribed": True,
                "price_rub": SUBSCRIPTION_PRICE_RUB,
            }

        if action == "contest":
            ref = params.get("ref", [""])[0]
            uid = get_user_id(init_data)

            contest = next((c for c in contests.values() if c["ref_code"] == ref and c["status"] == "active"), None)
            if not contest:
                return 404, {"error": "contest not found"}

            cid = contest["id"]
            participant = next((p for p in participants.values() if p["contest_id"] == cid and p["user_id"] == uid), None)
            if not participant:
                pid = next_participant_id
                next_participant_id += 1
                participant = {
                    "id": pid,
                    "contest_id": cid,
                    "user_id": uid,
                    "status": "checking",
                    "joined_at": datetime.now(timezone.utc).isoformat(),
                }
                participants[pid] = participant

            conds = [c for c in conditions.values() if c["contest_id"] == cid]
            conds.sort(key=lambda x: x["sort_order"])
            checks = {
                chk["condition_id"]: chk["status"]
                for chk in condition_checks.values()
                if chk["participant_id"] == participant["id"]
            }

            return 200, {
                "contest": {
                    "title": contest["title"],
                    "description": contest["description"],
                    "deadline_at": contest["deadline_at"],
                },
                "participant_id": participant["id"],
                "status": participant["status"],
                "conditions": [
                    {
                        "id": c["id"],
                        "type": c["type"],
                        "description": c["description"],
                        "link": c.get("link"),
                        "status": checks.get(c["id"], "pending"),
                    }
                    for c in conds
                ],
            }

        if action == "list_contests":
            uid = get_user_id(init_data)
            base_url = MINIAPP_URL.rstrip("/") if MINIAPP_URL else ""
            my_contests = [
                {
                    "id": c["id"],
                    "title": c["title"],
                    "status": c["status"],
                    "deadline_at": c["deadline_at"],
                    "ref_link": f"{base_url}/?ref={c['ref_code']}",
                }
                for c in contests.values()
                if c["owner_user_id"] == uid
            ]
            return 200, {"contests": my_contests}

        return 400, {"error": f"unknown action: {action}"}

    if method == "POST":
        if action == "telegram_webhook":
            update = body
            message = update.get("message") or update.get("edited_message")
            if not message:
                return 200, {"ok": True}

            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            from_user = message.get("from") or {}
            user_id = from_user.get("id") or chat_id
            first_name = from_user.get("first_name", "Участник")
            text = (message.get("text") or "").strip()
            photo = message.get("photo")

            # Determine public domain
            host = headers.get("x-forwarded-host") or headers.get("host") or ""
            proto = headers.get("x-forwarded-proto") or "https"
            if host and "localhost" not in host:
                base_miniapp = f"{proto}://{host}"
            else:
                base_miniapp = MINIAPP_URL.rstrip("/")

            reply_text = ""
            reply_markup = None

            if text.startswith("/start"):
                parts = text.split(maxsplit=1)
                ref_param = parts[1].strip() if len(parts) > 1 else ""

                ref_code = ""
                if ref_param.startswith("c_"):
                    ref_code = ref_param[2:]
                elif ref_param.startswith("ref_"):
                    ref_code = ref_param[4:]
                elif ref_param:
                    ref_code = ref_param

                if ref_code:
                    contest_url = f"{base_miniapp}/?ref={ref_code}"
                    reply_text = (
                        f"🎁 <b>Здравствуйте, {first_name}!</b>\n\n"
                        f"Вы приглашены к участию в розыгрыше призов!\n\n"
                        f"Чтобы подтвердить участие и побороться за ценные призы, "
                        f"нажмите на кнопку ниже и выполните условия чек-листа:"
                    )
                    reply_markup = {
                        "inline_keyboard": [
                            [
                                {"text": "🎉 Участвовать в конкурсе", "web_app": {"url": contest_url}}
                            ]
                        ]
                    }
                else:
                    app_url = f"{base_miniapp}/"
                    admin_url = f"{base_miniapp}/?admin=1"
                    buttons = [
                        [{"text": "🎁 Открыть конкурсы", "web_app": {"url": app_url}}]
                    ]
                    if user_id == ADMIN_ID or str(user_id) == str(ADMIN_ID):
                        buttons.append([{"text": "⚙️ Создать конкурс (Админ)", "web_app": {"url": admin_url}}])

                    reply_text = (
                        f"👋 <b>Привет, {first_name}!</b>\n\n"
                        f"Добро пожаловать в Telegram-бота конкурсов и розыгрышей!\n\n"
                        f"✨ <b>Возможности:</b>\n"
                        f"• Участвуйте в розыгрышах ценных призов\n"
                        f"• Выполняйте простые условия (подписка, активность, скриншоты)\n"
                        f"• Создавайте свои собственные конкурсы через удобный Mini App\n\n"
                        f"Нажмите кнопку ниже, чтобы открыть приложение:"
                    )
                    reply_markup = {"inline_keyboard": buttons}

            elif text.startswith("/admin"):
                admin_url = f"{base_miniapp}/?admin=1"
                reply_text = (
                    "⚙️ <b>Панель управления конкурсами</b>\n\n"
                    "Здесь вы можете:\n"
                    "• Создавать новые розыгрыши с призовыми местами\n"
                    "• Настраивать условия чек-листа (каналы, скриншоты)\n"
                    "• Получать реферальные ссылки для участников\n"
                    "• Управлять подпиской организатора\n\n"
                    "Нажмите кнопку ниже, чтобы открыть админку:"
                )
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "📊 Открыть админ-панель", "web_app": {"url": admin_url}}]
                    ]
                }

            elif photo:
                reply_text = (
                    "📸 <b>Скриншот получен!</b>\n\n"
                    "Он передан организаторам конкурса на ручную проверку. "
                    "После подтверждения статус задания обновится в чек-листе Mini App."
                )
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "🔍 Открыть чек-лист в Mini App", "web_app": {"url": f"{base_miniapp}/"}}]
                    ]
                }

            else:
                reply_text = (
                    "👋 Чтобы принять участие в конкурсе или управлять розыгрышами, откройте Mini App:"
                )
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "🚀 Открыть приложение", "web_app": {"url": f"{base_miniapp}/"}}]
                    ]
                }

            if chat_id:
                payload = {
                    "chat_id": chat_id,
                    "text": reply_text,
                    "parse_mode": "HTML",
                }
                if reply_markup:
                    payload["reply_markup"] = reply_markup
                send_telegram_api("sendMessage", payload)

                # Also return response in webhook payload for direct Telegram execution
                resp = {
                    "method": "sendMessage",
                    "chat_id": chat_id,
                    "text": reply_text,
                    "parse_mode": "HTML",
                }
                if reply_markup:
                    resp["reply_markup"] = reply_markup
                return 200, resp

            return 200, {"ok": True}

        if action == "check_condition":
            pid = int(body.get("participant_id", 0))
            cid = int(body.get("condition_id", 0))
            participant = participants.get(pid)
            if not participant:
                return 404, {"error": "participant not found"}

            cond = conditions.get(cid)
            if not cond:
                return 404, {"error": "condition not found"}

            new_status = "approved" if cond["type"] == "auto_channel_sub" else "awaiting_screenshot"
            chk = next(
                (c for c in condition_checks.values() if c["participant_id"] == pid and c["condition_id"] == cid),
                None,
            )
            if not chk:
                chk_id = next_check_id
                next_check_id += 1
                condition_checks[chk_id] = {
                    "id": chk_id,
                    "participant_id": pid,
                    "condition_id": cid,
                    "status": "approved" if cond["type"] == "auto_channel_sub" else "pending",
                }
            else:
                if cond["type"] == "auto_channel_sub":
                    chk["status"] = "approved"

            all_conds = [c for c in conditions.values() if c["contest_id"] == participant["contest_id"]]
            approved_checks = [
                c for c in condition_checks.values() if c["participant_id"] == pid and c["status"] == "approved"
            ]
            confirmed = len(all_conds) > 0 and len(approved_checks) >= len(all_conds)
            if confirmed:
                participant["status"] = "confirmed"

            return 200, {"condition_status": new_status, "contest_confirmed": confirmed}

        if action == "create_contest":
            uid = get_user_id(init_data)
            ref_code = secrets.token_hex(4)
            cid = next_contest_id
            next_contest_id += 1

            deadline = body.get("deadline_at") or (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
            contests[cid] = {
                "id": cid,
                "ref_code": ref_code,
                "owner_user_id": uid,
                "title": body.get("title", "Новый конкурс"),
                "description": body.get("description", ""),
                "status": "active",
                "deadline_at": deadline,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            for p in body.get("places", []):
                pid = len(prize_places) + 1
                prize_places[pid] = {
                    "id": pid,
                    "contest_id": cid,
                    "place_number": int(p.get("place", 1)),
                    "prize_text": str(p.get("prize", "")),
                    "capacity": int(p["capacity"]) if p.get("capacity") else None,
                }

            for i, c in enumerate(body.get("conditions", [])):
                cond_id = next_condition_id
                next_condition_id += 1
                conditions[cond_id] = {
                    "id": cond_id,
                    "contest_id": cid,
                    "type": c.get("type", "auto_channel_sub"),
                    "description": c.get("description", "Условие участия"),
                    "channel_id": int(c["channel_id"]) if c.get("channel_id") else None,
                    "link": c.get("link"),
                    "sort_order": i,
                }

            base_url = MINIAPP_URL.rstrip("/") if MINIAPP_URL else ""
            return 200, {"ref_link": f"{base_url}/?ref={ref_code}", "ref_code": ref_code}

        if action == "create_invoice":
            inv_id = f"inv_{secrets.token_hex(4)}"
            return 200, {"pay_url": f"https://t.me/CryptoBot?start={inv_id}", "invoice_id": inv_id}

        if action == "check_payment":
            return 200, {"paid": True}

        return 400, {"error": f"unknown action: {action}"}

    return 405, {"error": "method not allowed"}


# --- WSGI callable for Vercel automatic detection (app) ---
def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    query_string = environ.get("QUERY_STRING", "")

    content_length = int(environ.get("CONTENT_LENGTH") or 0)
    body_bytes = environ["wsgi.input"].read(content_length) if content_length > 0 else b""

    headers = {
        "authorization": environ.get("HTTP_AUTHORIZATION", ""),
        "content-type": environ.get("CONTENT_TYPE", ""),
    }

    status_code, result = process_api_request(method, query_string, body_bytes, headers)

    body = json.dumps(result).encode("utf-8")
    status_str = f"{status_code} OK" if status_code == 200 else f"{status_code} Error"

    response_headers = [
        ("Content-Type", "application/json"),
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type, Authorization"),
        ("Content-Length", str(len(body))),
    ]
    start_response(status_str, response_headers)
    return [body]


# --- BaseHTTPRequestHandler for legacy Vercel runtime (handler) ---
class handler(BaseHTTPRequestHandler):
    def _handle_all(self, method: str):
        parsed = urlparse(self.path)
        length = int(self.headers.get("content-length", 0))
        body_bytes = self.rfile.read(length) if length > 0 else b""
        headers = {
            "authorization": self.headers.get("authorization", ""),
            "content-type": self.headers.get("content-type", ""),
        }

        status, payload = process_api_request(method, parsed.query, body_bytes, headers)

        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._handle_all("OPTIONS")

    def do_GET(self):
        self._handle_all("GET")

    def do_POST(self):
        self._handle_all("POST")
