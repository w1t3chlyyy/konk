import hashlib
import hmac
import json
import os
import secrets
import urllib.request
import urllib.error
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ADMIN_ID = int(os.environ.get("ADMIN_ID", "123456789"))
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
# NOTE: this must be a *separate* value from CRON_SECRET. Telegram's webhook
# secret_token is a different mechanism (it's echoed back in the
# X-Telegram-Bot-Api-Secret-Token header only if you explicitly pass
# secret_token= when calling setWebhook). If you don't set this env var, no
# check is performed — which matches the plain `setWebhook?url=...` call in
# the README. If you DO want this protection, set TELEGRAM_WEBHOOK_SECRET
# AND re-run setWebhook with &secret_token=<same value>.
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
vercel_url = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or os.environ.get("VERCEL_URL")
default_miniapp_url = f"https://{vercel_url}" if vercel_url else "http://localhost:3000"
MINIAPP_URL = os.environ.get("MINIAPP_URL") or default_miniapp_url
CRYPTOBOT_TOKEN = os.environ.get("CRYPTOBOT_TOKEN", "")
CRYPTOBOT_API_URL = "https://pay.crypt.bot/api"
SUBSCRIPTION_DAYS = 30
# Only used if you genuinely have no DB configured for local hacking; never
# used when DATABASE_URL is set (i.e. never in a real deployment).
DEV_NO_DB_MODE = not DATABASE_URL


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str | None = None):
        super().__init__(error)
        self.status = status
        self.payload = {"error": error}
        if message:
            self.payload["message"] = message


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
@contextmanager
def db():
    """Short-lived connection per request — safe for serverless + pgbouncer
    transaction pooling (Supabase 'Transaction pooler', port 6543)."""
    if not DATABASE_URL:
        raise ApiError(500, "database_not_configured",
                        "DATABASE_URL is not set. Follow README section 1 (Supabase) "
                        "and run sql/schema.sql, then set DATABASE_URL in Vercel env vars.")
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor,
                             connect_timeout=8)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_admin_subscription(cur):
    cur.execute(
        """insert into subscriptions (user_id, active, lifetime, expires_at)
           values (%s, true, true, null)
           on conflict (user_id) do nothing""",
        (ADMIN_ID,),
    )


def ensure_schema_extensions(cur):
    """Additive, idempotent schema patches so the app self-heals even if
    sql/schema.sql in the repo hasn't been re-run in Supabase after an
    update. Safe to run on every request (cheap no-ops once applied)."""
    cur.execute("alter table condition_checks add column if not exists file_id text")
    cur.execute(
        """create table if not exists pending_screenshots (
               user_id bigint primary key,
               participant_id integer not null,
               condition_id integer not null,
               contest_id integer not null,
               created_at timestamptz not null default now()
           )"""
    )


SETTINGS_DEFAULTS = {
    "welcome_text": "",
    "welcome_photo_url": "",
    "subscription_price_rub": 390,
    "bot_username": "",
}


def get_settings(cur) -> dict:
    """Always returns a dict with every expected key present, even if the
    bot_settings table in the DB is missing a column (e.g. an older/partial
    schema) or has no row yet."""
    cur.execute("select * from bot_settings where id = 1")
    row = cur.fetchone()
    if not row:
        cur.execute("insert into bot_settings (id) values (1) returning *")
        row = cur.fetchone()
    settings = dict(SETTINGS_DEFAULTS)
    if row:
        settings.update({k: v for k, v in dict(row).items() if v is not None})
    return settings


def update_settings(cur, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k} = %s" for k in fields)
    cur.execute(f"update bot_settings set {sets} where id = 1", list(fields.values()))


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
def send_telegram_api(method: str, payload: dict):
    if not BOT_TOKEN:
        print("[Telegram API] Warning: BOT_TOKEN is not configured")
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "ContestBot/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"[Telegram API] HTTP error in {method}: {e.code} {e.read()}")
        return None
    except Exception as e:
        print(f"[Telegram API] Error in {method}: {e}")
        return None


def check_channel_subscription(channel_id: int, user_id: int) -> bool:
    """Real membership check via Telegram Bot API. The bot must be an admin
    of the channel for getChatMember to work on private/broadcast channels."""
    if not BOT_TOKEN or not channel_id:
        # Can't verify without a bot token/channel id — fail closed in prod,
        # but don't hard-block local dev without a token.
        return not BOT_TOKEN and DEV_NO_DB_MODE
    result = send_telegram_api("getChatMember", {"chat_id": channel_id, "user_id": user_id})
    if not result or not result.get("ok"):
        return False
    status = (result.get("result") or {}).get("status")
    return status in ("creator", "administrator", "member")


def get_cached_bot_username(cur) -> str:
    settings = get_settings(cur)
    if settings.get("bot_username"):
        return settings["bot_username"]
    if BOT_TOKEN:
        me = send_telegram_api("getMe", {})
        if me and me.get("ok") and me.get("result", {}).get("username"):
            uname = me["result"]["username"]
            update_settings(cur, bot_username=uname)
            return uname
    return "RandomizerGiftRobot"


def get_contest_share_link(cur, ref_code: str) -> str:
    uname = get_cached_bot_username(cur)
    return f"https://t.me/{uname}?start=c_{ref_code}"


def condition_status_for_client(condition: dict, db_status: str | None) -> str:
    """Maps the raw DB status of a condition_check row (or its absence) onto
    the status vocabulary the Mini App front-end understands, so state
    ('awaiting review', 'rejected') survives a page reload instead of only
    existing as a one-off response to check_condition."""
    if db_status == "approved":
        return "approved"
    if db_status == "rejected":
        return "rejected"
    if db_status == "pending" and condition["type"] == "manual_screenshot":
        return "awaiting_screenshot"
    return "pending"


# ---------------------------------------------------------------------------
# Telegram WebApp init_data verification (HMAC-SHA256, per Telegram docs)
# ---------------------------------------------------------------------------
def validate_init_data(init_data: str) -> dict | None:
    """Returns the verified user dict, or None if the signature is invalid."""
    if not init_data:
        return None
    try:
        params = parse_qs(init_data, keep_blank_values=True)
        flat = {k: v[0] for k, v in params.items()}
        received_hash = flat.pop("hash", None)
        if not received_hash:
            return None

        if not BOT_TOKEN:
            # No bot token configured (local dev only) — cannot verify a real
            # signature. Trust the payload only in that explicit dev scenario.
            user_raw = flat.get("user")
            return json.loads(user_raw) if user_raw else None

        data_check_string = "\n".join(f"{k}={flat[k]}" for k in sorted(flat.keys()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            return None

        user_raw = flat.get("user")
        return json.loads(user_raw) if user_raw else None
    except Exception as e:
        print(f"[init_data] validation error: {e}")
        return None


def get_verified_user(init_data: str) -> dict:
    """Like get_verified_user_id, but returns the full Telegram user object
    (id, username, first_name, ...) — used wherever we want a human-readable
    name for organizer notifications."""
    user = validate_init_data(init_data)
    if not user or "id" not in user:
        raise ApiError(401, "invalid_init_data", "Telegram authentication failed")
    return user


def get_verified_user_id(init_data: str) -> int:
    return int(get_verified_user(init_data)["id"])


def describe_user(user: dict) -> str:
    if user.get("username"):
        return f"@{user['username']}"
    if user.get("first_name"):
        return user["first_name"]
    return f"id {user.get('id')}"


def describe_telegram_user_by_id(user_id: int) -> str:
    """Best-effort lookup for cases where we only have a bare user_id (e.g.
    a callback triggered by reviewing a screenshot) and no cached username."""
    try:
        info = send_telegram_api("getChat", {"chat_id": user_id})
        if info and info.get("ok"):
            result = info["result"]
            if result.get("username"):
                return f"@{result['username']}"
            if result.get("first_name"):
                return result["first_name"]
    except Exception:
        pass
    return f"id {user_id}"


# ---------------------------------------------------------------------------
# Organizer notifications (purely informational stats pings — failures here
# must never break the actual request the participant is making)
# ---------------------------------------------------------------------------
def notify_new_participant(cur, contest: dict, who: str, user_id: int):
    try:
        cur.execute("select count(*) as n from participants where contest_id = %s", (contest["id"],))
        total = cur.fetchone()["n"]
        send_telegram_api("sendMessage", {
            "chat_id": contest["owner_user_id"],
            "text": (
                f"👀 <b>Новый участник конкурса «{contest['title']}»</b>\n\n"
                f"{who} (id {user_id}) открыл(а) чек-лист.\n\n"
                f"Всего участников: <b>{total}</b>"
            ),
            "parse_mode": "HTML",
        })
    except Exception as e:
        print(f"[notify_new_participant] {e}")


def notify_participant_confirmed(cur, contest: dict, who: str, user_id: int):
    try:
        cur.execute("select count(*) as n from participants where contest_id = %s", (contest["id"],))
        total = cur.fetchone()["n"]
        cur.execute(
            "select count(*) as n from participants where contest_id = %s and status = 'confirmed'",
            (contest["id"],),
        )
        confirmed_n = cur.fetchone()["n"]
        send_telegram_api("sendMessage", {
            "chat_id": contest["owner_user_id"],
            "text": (
                f"✅ <b>Участник выполнил все условия!</b>\n\n"
                f"Конкурс: «{contest['title']}»\n"
                f"{who} (id {user_id})\n\n"
                f"Подтверждено: <b>{confirmed_n}</b> из {total} участников"
            ),
            "parse_mode": "HTML",
        })
    except Exception as e:
        print(f"[notify_participant_confirmed] {e}")


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------
def is_user_subscribed(cur, user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    cur.execute("select active, lifetime, expires_at from subscriptions where user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row:
        return False
    if row["lifetime"]:
        return True
    if not row["active"]:
        return False
    if not row["expires_at"]:
        return False
    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


def activate_subscription(cur, user_id: int, days: int = SUBSCRIPTION_DAYS):
    cur.execute("select lifetime, expires_at from subscriptions where user_id = %s", (user_id,))
    row = cur.fetchone()
    now = datetime.now(timezone.utc)
    base = now
    if row and row["expires_at"]:
        expires_at = row["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > now:
            base = expires_at
    new_expiry = base + timedelta(days=days)
    lifetime = bool(row["lifetime"]) if row else False
    cur.execute(
        """insert into subscriptions (user_id, active, lifetime, expires_at)
           values (%s, true, %s, %s)
           on conflict (user_id) do update
             set active = true, expires_at = excluded.expires_at""",
        (user_id, lifetime, new_expiry),
    )


# ---------------------------------------------------------------------------
# CryptoBot (Crypto Pay API)
# ---------------------------------------------------------------------------
def cryptobot_request(method: str, payload: dict):
    if not CRYPTOBOT_TOKEN:
        return None
    url = f"{CRYPTOBOT_API_URL}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[CryptoBot] {method} error: {e}")
        return None


def create_crypto_invoice(amount_rub: int, payload: str, description: str) -> dict:
    if CRYPTOBOT_TOKEN:
        data = cryptobot_request("createInvoice", {
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": str(amount_rub),
            "description": description,
            "payload": payload,
        })
        if data and data.get("ok"):
            return {"invoice_id": str(data["result"]["invoice_id"]), "pay_url": data["result"]["pay_url"]}
        raise ApiError(502, "cryptobot_error", "Could not create invoice with CryptoBot")

    # No token configured: local-dev mock only. This path never runs in a
    # real deployment with CRYPTOBOT_TOKEN set.
    invoice_id = f"mock_{secrets.token_hex(6)}"
    return {"invoice_id": invoice_id, "pay_url": f"https://t.me/CryptoBot?start={invoice_id}"}


def check_crypto_invoice_status(invoice_id: str) -> str:
    if CRYPTOBOT_TOKEN and not invoice_id.startswith("mock_"):
        data = cryptobot_request("getInvoices", {"invoice_ids": invoice_id})
        if data and data.get("ok") and data["result"].get("items"):
            return data["result"]["items"][0]["status"]
        return "unknown"
    # mock invoices (no CryptoBot token) auto-confirm so local dev is usable
    return "paid" if invoice_id.startswith("mock_") else "unknown"


# ---------------------------------------------------------------------------
# Contest finalization (real logic, used by GET ?action=cron)
# ---------------------------------------------------------------------------
def finalize_contest(cur, contest_id: int):
    cur.execute("select * from contests where id = %s and status = 'active'", (contest_id,))
    contest = cur.fetchone()
    if not contest:
        return

    cur.execute(
        "select * from prize_places where contest_id = %s order by place_number asc",
        (contest_id,),
    )
    places = cur.fetchall()

    cur.execute(
        """select * from participants where contest_id = %s and status = 'confirmed'
           order by coalesce(confirmed_at, joined_at) asc""",
        (contest_id,),
    )
    confirmed = cur.fetchall()

    idx = 0
    for place in places:
        cap = place["capacity"]
        if cap is None:
            for p in confirmed[idx:]:
                cur.execute("update participants set assigned_place = %s where id = %s",
                            (place["place_number"], p["id"]))
            idx = len(confirmed)
            break
        else:
            for p in confirmed[idx:idx + cap]:
                cur.execute("update participants set assigned_place = %s where id = %s",
                            (place["place_number"], p["id"]))
            idx += cap

    cur.execute("update contests set status = 'finished' where id = %s", (contest_id,))

    # Best-effort notification to winners; failures here must not roll back
    # the finalize transaction.
    cur.execute(
        """select p.user_id, p.assigned_place, pp.prize_text
           from participants p
           left join prize_places pp on pp.contest_id = p.contest_id and pp.place_number = p.assigned_place
           where p.contest_id = %s and p.status = 'confirmed'""",
        (contest_id,),
    )
    for row in cur.fetchall():
        try:
            if row["assigned_place"]:
                send_telegram_api("sendMessage", {
                    "chat_id": row["user_id"],
                    "text": (
                        f"🎉 <b>Розыгрыш «{contest['title']}» завершён!</b>\n\n"
                        f"Ваш приз ({row['assigned_place']} место): "
                        f"<b>{row['prize_text'] or '—'}</b>\n\n"
                        f"Организатор свяжется с вами для вручения."
                    ),
                    "parse_mode": "HTML",
                })
        except Exception as e:
            print(f"[finalize] notify error for user {row['user_id']}: {e}")


# ---------------------------------------------------------------------------
# Main request handling
# ---------------------------------------------------------------------------
def process_api_request(method: str, query_string: str, body_bytes: bytes, headers: dict) -> tuple[int, dict]:
    params = parse_qs(query_string)
    action = params.get("action", [None])[0]
    init_data = params.get("init_data", [""])[0]

    body = {}
    if body_bytes:
        try:
            body = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            body = {}
    if not action:
        action = body.get("action")
    if not init_data:
        init_data = body.get("init_data", "")

    if method == "OPTIONS":
        return 204, {}

    try:
        with db() as conn:
            cur = conn.cursor()
            ensure_admin_subscription(cur)
            ensure_schema_extensions(cur)

            if method == "GET":
                return handle_get(cur, action, params, init_data, headers)
            if method == "POST":
                return handle_post(cur, action, body, init_data, headers)
            return 405, {"error": "method not allowed"}
    except ApiError as e:
        return e.status, e.payload
    except Exception:
        import traceback
        print(f"[process_api_request] {method} action={action} error:\n{traceback.format_exc()}")
        return 500, {"error": "internal_error"}


def handle_get(cur, action, params, init_data, headers) -> tuple[int, dict]:
    if action == "cron":
        auth = headers.get("authorization", "")
        if CRON_SECRET and auth != f"Bearer {CRON_SECRET}":
            return 401, {"error": "unauthorized"}
        cur.execute(
            "select id from contests where status = 'active' and deadline_at <= now()"
        )
        due = [row["id"] for row in cur.fetchall()]
        for contest_id in due:
            finalize_contest(cur, contest_id)
        return 200, {"finalized": len(due)}

    if action == "admin_status":
        uid = get_verified_user_id(init_data)
        settings = get_settings(cur)
        return 200, {
            "is_admin": uid == ADMIN_ID,
            "subscribed": is_user_subscribed(cur, uid),
            "price_rub": settings["subscription_price_rub"],
        }

    if action == "contest":
        ref = params.get("ref", [""])[0]
        user = get_verified_user(init_data)
        uid = int(user["id"])

        cur.execute("select * from contests where ref_code = %s", (ref,))
        contest = cur.fetchone()
        if not contest:
            return 404, {"error": "contest not found"}
        if contest["status"] == "draft":
            return 400, {"error": "contest in draft",
                         "message": "Этот конкурс сохранён как черновик и ещё не опубликован организатором."}

        cur.execute(
            "select * from participants where contest_id = %s and user_id = %s",
            (contest["id"], uid),
        )
        participant = cur.fetchone()
        if not participant:
            cur.execute(
                """insert into participants (contest_id, user_id, status)
                   values (%s, %s, 'checking') returning *""",
                (contest["id"], uid),
            )
            participant = cur.fetchone()
            notify_new_participant(cur, contest, describe_user(user), uid)

        cur.execute(
            "select * from conditions where contest_id = %s order by sort_order asc",
            (contest["id"],),
        )
        conds = cur.fetchall()

        cur.execute(
            "select condition_id, status from condition_checks where participant_id = %s",
            (participant["id"],),
        )
        checks = {row["condition_id"]: row["status"] for row in cur.fetchall()}

        # A specific prize is only ever revealed once the contest has
        # actually been finalized by the cron job (deadline reached, places
        # distributed by finalize_contest). Before that, a confirmed
        # participant only learns that they're "in" the giveaway — never a
        # guessed place/prize that might not match what they actually get.
        prize_won = None
        place_won = None
        if contest["status"] == "finished" and participant["status"] == "confirmed":
            place_won = participant["assigned_place"]
            if place_won:
                cur.execute(
                    "select prize_text from prize_places where contest_id = %s and place_number = %s",
                    (contest["id"], place_won),
                )
                matched = cur.fetchone()
                if matched:
                    prize_won = matched["prize_text"]

        return 200, {
            "contest": {
                "id": contest["id"],
                "title": contest["title"],
                "description": contest["description"],
                "deadline_at": contest["deadline_at"].isoformat(),
                "status": contest["status"],
            },
            "participant_id": participant["id"],
            "status": participant["status"],
            "assigned_place": place_won,
            "prize_won": prize_won,
            "conditions": [
                {
                    "id": c["id"],
                    "type": c["type"],
                    "description": c["description"],
                    "link": c["link"],
                    "status": condition_status_for_client(c, checks.get(c["id"])),
                }
                for c in conds
            ],
        }

    if action == "list_contests":
        uid = get_verified_user_id(init_data)
        cur.execute(
            "select * from contests where owner_user_id = %s order by created_at desc",
            (uid,),
        )
        rows = cur.fetchall()
        return 200, {
            "contests": [
                {
                    "id": c["id"],
                    "title": c["title"],
                    "status": c["status"],
                    "deadline_at": c["deadline_at"].isoformat(),
                    "ref_link": get_contest_share_link(cur, c["ref_code"]),
                }
                for c in rows
            ]
        }

    return 400, {"error": f"unknown action: {action}"}


def handle_post(cur, action, body, init_data, headers) -> tuple[int, dict]:
    if action == "telegram_webhook":
        return handle_telegram_webhook(cur, body, headers)

    if action == "check_condition":
        user = get_verified_user(init_data)
        uid = int(user["id"])
        pid = int(body.get("participant_id", 0))
        cid = int(body.get("condition_id", 0))

        cur.execute("select * from participants where id = %s", (pid,))
        participant = cur.fetchone()
        if not participant or participant["user_id"] != uid:
            return 403, {"error": "forbidden"}

        cur.execute("select * from conditions where id = %s", (cid,))
        cond = cur.fetchone()
        if not cond:
            return 404, {"error": "condition not found"}

        if cond["type"] == "auto_channel_sub":
            subscribed = check_channel_subscription(cond["channel_id"], uid)
            if not subscribed:
                return 200, {"condition_status": "not_subscribed", "contest_confirmed": False}
            new_status = "approved"
            cur.execute(
                """insert into condition_checks (participant_id, condition_id, status, reviewed_at)
                   values (%s, %s, 'approved', now())
                   on conflict (participant_id, condition_id)
                   do update set status = 'approved', reviewed_at = now()""",
                (pid, cid),
            )
        else:
            new_status = "awaiting_screenshot"
            # Re-arm on every click: if this was previously 'rejected' (or is
            # brand new) it goes back to 'pending' awaiting a fresh photo.
            # An already-'approved' row is left untouched.
            cur.execute(
                """insert into condition_checks (participant_id, condition_id, status)
                   values (%s, %s, 'pending')
                   on conflict (participant_id, condition_id)
                   do update set status = case
                       when condition_checks.status = 'approved' then 'approved'
                       else 'pending'
                   end""",
                (pid, cid),
            )
            # Remember which (participant, condition) this user's *next*
            # photo message in the bot chat belongs to — this is what the
            # webhook photo handler below reads to know who to notify.
            cur.execute(
                """insert into pending_screenshots (user_id, participant_id, condition_id, contest_id)
                   values (%s, %s, %s, %s)
                   on conflict (user_id) do update set
                       participant_id = excluded.participant_id,
                       condition_id = excluded.condition_id,
                       contest_id = excluded.contest_id,
                       created_at = now()""",
                (uid, pid, cid, participant["contest_id"]),
            )

        cur.execute("select count(*) as n from conditions where contest_id = %s", (participant["contest_id"],))
        total = cur.fetchone()["n"]
        cur.execute(
            "select count(*) as n from condition_checks where participant_id = %s and status = 'approved'",
            (pid,),
        )
        approved = cur.fetchone()["n"]
        confirmed = total > 0 and approved >= total
        newly_confirmed = confirmed and participant["status"] != "confirmed"
        if newly_confirmed:
            cur.execute(
                "update participants set status = 'confirmed', confirmed_at = now() where id = %s",
                (pid,),
            )
            cur.execute("select * from contests where id = %s", (participant["contest_id"],))
            owner_contest = cur.fetchone()
            if owner_contest:
                notify_participant_confirmed(cur, owner_contest, describe_user(user), uid)

        return 200, {"condition_status": new_status, "contest_confirmed": confirmed}

    if action == "create_contest":
        uid = get_verified_user_id(init_data)
        if not is_user_subscribed(cur, uid):
            return 402, {"error": "subscription required", "message": "Для создания конкурсов необходима подписка"}

        is_draft = bool(body.get("is_draft"))
        ref_code = secrets.token_hex(4)
        deadline_raw = body.get("deadline_at")
        deadline_at = deadline_raw if deadline_raw else (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        cur.execute(
            """insert into contests (ref_code, owner_user_id, title, description, status, deadline_at)
               values (%s, %s, %s, %s, %s, %s) returning id""",
            (ref_code, uid, body.get("title", "Новый конкурс"), body.get("description", ""),
             "draft" if is_draft else "active", deadline_at),
        )
        contest_id = cur.fetchone()["id"]

        for p in body.get("places", []):
            cur.execute(
                """insert into prize_places (contest_id, place_number, prize_text, capacity)
                   values (%s, %s, %s, %s)""",
                (contest_id, int(p.get("place", 1)), str(p.get("prize", "")),
                 int(p["capacity"]) if p.get("capacity") else None),
            )

        for i, c in enumerate(body.get("conditions", [])):
            cur.execute(
                """insert into conditions (contest_id, type, description, channel_id, link, sort_order)
                   values (%s, %s, %s, %s, %s, %s)""",
                (contest_id, c.get("type", "auto_channel_sub"), c.get("description", "Условие участия"),
                 int(c["channel_id"]) if c.get("channel_id") else None, c.get("link"), i),
            )

        status = "draft" if is_draft else "active"
        return 200, {
            "contest_id": contest_id,
            "status": status,
            "ref_link": get_contest_share_link(cur, ref_code),
            "ref_code": ref_code,
        }

    if action == "publish_contest":
        uid = get_verified_user_id(init_data)
        cid = int(body.get("contest_id", 0))
        cur.execute("select * from contests where id = %s", (cid,))
        contest = cur.fetchone()
        if not contest or contest["owner_user_id"] != uid:
            return 404, {"error": "contest not found"}
        cur.execute("update contests set status = 'active' where id = %s", (cid,))
        return 200, {"ok": True, "ref_link": get_contest_share_link(cur, contest["ref_code"])}

    if action == "create_invoice":
        uid = get_verified_user_id(init_data)
        settings = get_settings(cur)
        price = settings["subscription_price_rub"]
        invoice = create_crypto_invoice(price, str(uid), "Подписка на конструктор конкурсов — 30 дней")
        cur.execute(
            """insert into payments (user_id, invoice_id, amount, asset, status)
               values (%s, %s, %s, 'RUB', 'pending')""",
            (uid, invoice["invoice_id"], price),
        )
        return 200, {"pay_url": invoice["pay_url"], "invoice_id": invoice["invoice_id"]}

    if action == "check_payment":
        uid = get_verified_user_id(init_data)
        invoice_id = str(body.get("invoice_id", ""))
        cur.execute("select * from payments where invoice_id = %s", (invoice_id,))
        payment = cur.fetchone()
        if not payment or payment["user_id"] != uid:
            return 403, {"error": "forbidden"}
        if payment["status"] == "paid":
            return 200, {"paid": True}

        status = check_crypto_invoice_status(invoice_id)
        if status == "paid":
            cur.execute(
                "update payments set status = 'paid', paid_at = now() where invoice_id = %s",
                (invoice_id,),
            )
            activate_subscription(cur, uid)
            return 200, {"paid": True}

        return 200, {"paid": False}

    return 400, {"error": f"unknown action: {action}"}


def handle_telegram_webhook(cur, update: dict, headers: dict) -> tuple[int, dict]:
    # Only enforced if TELEGRAM_WEBHOOK_SECRET is explicitly set AND you
    # re-ran setWebhook with a matching &secret_token=. Do NOT default this
    # to CRON_SECRET — that silently blackholes every single update, since
    # Telegram will never send a header value it was never told to send.
    if WEBHOOK_SECRET:
        got = headers.get("x-telegram-bot-api-secret-token", "")
        if got != WEBHOOK_SECRET:
            return 401, {"error": "unauthorized"}

    callback_query = update.get("callback_query")
    message = update.get("message") or update.get("edited_message")

    host = headers.get("x-forwarded-host") or headers.get("host") or ""
    proto = headers.get("x-forwarded-proto") or "https"
    base_miniapp = f"{proto}://{host}" if host and "localhost" not in host else MINIAPP_URL.rstrip("/")

    if callback_query:
        cb_id = callback_query.get("id")
        from_user = callback_query.get("from") or {}
        user_id = from_user.get("id")
        cb_message = callback_query.get("message") or {}
        chat_id = cb_message.get("chat", {}).get("id")
        cb_data = callback_query.get("data", "")

        # --- Screenshot review (any contest owner can review their own
        # contest's submissions, not just the global bot owner) ---
        if cb_data.startswith("scr_ok:") or cb_data.startswith("scr_no:"):
            approve = cb_data.startswith("scr_ok:")
            check_id = int(cb_data.split(":", 1)[1])

            cur.execute(
                """select cc.id, cc.participant_id, p.contest_id, p.user_id as participant_user_id,
                          p.status as participant_status, c.owner_user_id, c.title as contest_title
                   from condition_checks cc
                   join participants p on p.id = cc.participant_id
                   join contests c on c.id = p.contest_id
                   where cc.id = %s""",
                (check_id,),
            )
            row = cur.fetchone()

            send_telegram_api("answerCallbackQuery", {"callback_query_id": cb_id})

            if not row or (str(user_id) != str(row["owner_user_id"]) and str(user_id) != str(ADMIN_ID)):
                return 200, {"ok": True}

            new_status = "approved" if approve else "rejected"
            cur.execute(
                "update condition_checks set status = %s, reviewed_at = now() where id = %s",
                (new_status, check_id),
            )

            participant_id = row["participant_id"]
            cur.execute("select count(*) as n from conditions where contest_id = %s", (row["contest_id"],))
            total = cur.fetchone()["n"]
            cur.execute(
                "select count(*) as n from condition_checks where participant_id = %s and status = 'approved'",
                (participant_id,),
            )
            approved_n = cur.fetchone()["n"]
            confirmed = total > 0 and approved_n >= total
            newly_confirmed = confirmed and row["participant_status"] != "confirmed"
            if confirmed:
                cur.execute(
                    "update participants set status = 'confirmed', confirmed_at = now() "
                    "where id = %s and status != 'confirmed'",
                    (participant_id,),
                )
                if newly_confirmed:
                    cur.execute("select * from contests where id = %s", (row["contest_id"],))
                    owner_contest = cur.fetchone()
                    if owner_contest:
                        notify_participant_confirmed(
                            cur, owner_contest,
                            describe_telegram_user_by_id(row["participant_user_id"]),
                            row["participant_user_id"],
                        )

            verdict_emoji = "✅" if approve else "❌"
            verdict_text = "подтверждён" if approve else "отклонён"
            try:
                send_telegram_api("editMessageCaption", {
                    "chat_id": chat_id,
                    "message_id": cb_message.get("message_id"),
                    "caption": (cb_message.get("caption") or "") + f"\n\n{verdict_emoji} Скриншот {verdict_text}",
                    "parse_mode": "HTML",
                })
            except Exception as e:
                print(f"[scr_review] editMessageCaption failed: {e}")

            notify_text = (
                f"✅ <b>Ваш скриншот по конкурсу «{row['contest_title']}» подтверждён!</b>"
                if approve else
                f"❌ <b>Скриншот по конкурсу «{row['contest_title']}» отклонён организатором.</b>\n\n"
                f"Откройте чек-лист в приложении и отправьте другой скриншот."
            )
            send_telegram_api("sendMessage", {
                "chat_id": row["participant_user_id"], "text": notify_text, "parse_mode": "HTML",
            })
            if confirmed:
                send_telegram_api("sendMessage", {
                    "chat_id": row["participant_user_id"],
                    "text": "🎉 <b>Все условия выполнены — вы участвуете в розыгрыше!</b>",
                    "parse_mode": "HTML",
                })
            return 200, {"ok": True}

        # --- Bot-owner-only settings callbacks (unchanged behaviour) ---
        send_telegram_api("answerCallbackQuery", {"callback_query_id": cb_id})

        if str(user_id) != str(ADMIN_ID):
            send_telegram_api("sendMessage", {
                "chat_id": chat_id,
                "text": "⛔ <b>Доступ запрещен:</b> только главный администратор бота может менять эти настройки.",
                "parse_mode": "HTML",
            })
            return 200, {"ok": True}

        settings = get_settings(cur)

        if cb_data == "adm_edit_text":
            cur.execute(
                "insert into admin_chat_states (chat_id, state) values (%s, 'waiting_welcome_text') "
                "on conflict (chat_id) do update set state = excluded.state",
                (chat_id,),
            )
            send_telegram_api("sendMessage", {
                "chat_id": chat_id,
                "text": ("✏️ <b>Отправьте новый текст приветствия</b> для команды /start.\n\n"
                         "💡 <i>Можно использовать HTML и тег {first_name}.</i>\n\nДля отмены отправьте /cancel"),
                "parse_mode": "HTML",
            })
        elif cb_data == "adm_edit_photo":
            cur.execute(
                "insert into admin_chat_states (chat_id, state) values (%s, 'waiting_welcome_photo') "
                "on conflict (chat_id) do update set state = excluded.state",
                (chat_id,),
            )
            send_telegram_api("sendMessage", {
                "chat_id": chat_id,
                "text": ("🖼️ <b>Отправьте изображение</b> (или ссылку на фото).\n\n"
                         "Отправьте <code>none</code>, чтобы убрать фото, или /cancel."),
                "parse_mode": "HTML",
            })
        elif cb_data == "adm_edit_price":
            cur.execute(
                "insert into admin_chat_states (chat_id, state) values (%s, 'waiting_sub_price') "
                "on conflict (chat_id) do update set state = excluded.state",
                (chat_id,),
            )
            send_telegram_api("sendMessage", {
                "chat_id": chat_id,
                "text": (f"💰 <b>Введите новую стоимость подписки (в рублях)</b>:\n\n"
                         f"Текущая цена: <b>{settings['subscription_price_rub']}₽</b>\n\nДля отмены отправьте /cancel"),
                "parse_mode": "HTML",
            })
        elif cb_data == "adm_settings":
            has_photo = "Установлено ✅" if settings["welcome_photo_url"] else "Не установлено ❌"
            send_telegram_api("sendMessage", {
                "chat_id": chat_id,
                "text": (f"⚙️ <b>Настройки бота</b>\n\n💵 Цена подписки: {settings['subscription_price_rub']}₽ / 30 дней\n"
                         f"🖼️ Фото приветствия: {has_photo}\n\nВыберите действие:"),
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": [
                    [{"text": "✏️ Изменить текст приветствия", "callback_data": "adm_edit_text"}],
                    [{"text": "🖼️ Изменить фото приветствия", "callback_data": "adm_edit_photo"}],
                    [{"text": "💰 Изменить цену подписки", "callback_data": "adm_edit_price"}],
                    [{"text": "🚀 Открыть конструктор (Mini App)", "web_app": {"url": f"{base_miniapp}/?admin=1"}}],
                ]},
            })
        return 200, {"ok": True}

    if not message:
        return 200, {"ok": True}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    from_user = message.get("from") or {}
    user_id = from_user.get("id") or chat_id
    first_name = from_user.get("first_name", "Участник")
    text = (message.get("text") or "").strip()
    photo = message.get("photo")

    cur.execute("select state from admin_chat_states where chat_id = %s", (chat_id,))
    state_row = cur.fetchone()
    admin_state = state_row["state"] if state_row else None

    if admin_state and str(user_id) == str(ADMIN_ID):
        if text == "/cancel":
            cur.execute("delete from admin_chat_states where chat_id = %s", (chat_id,))
            send_telegram_api("sendMessage", {"chat_id": chat_id, "text": "❌ Действие отменено."})
            return 200, {"ok": True}

        if admin_state == "waiting_welcome_text" and text:
            update_settings(cur, welcome_text=text)
            cur.execute("delete from admin_chat_states where chat_id = %s", (chat_id,))
            send_telegram_api("sendMessage", {
                "chat_id": chat_id,
                "text": "✅ <b>Текст приветствия обновлён!</b>",
                "parse_mode": "HTML",
            })
            return 200, {"ok": True}

        if admin_state == "waiting_welcome_photo":
            cur.execute("delete from admin_chat_states where chat_id = %s", (chat_id,))
            if photo:
                file_id = photo[-1]["file_id"]
                update_settings(cur, welcome_photo_url=file_id)
                send_telegram_api("sendMessage", {"chat_id": chat_id, "text": "✅ <b>Фото сохранено!</b>", "parse_mode": "HTML"})
            elif text.lower() in ("none", "нет", "удалить"):
                update_settings(cur, welcome_photo_url="")
                send_telegram_api("sendMessage", {"chat_id": chat_id, "text": "✅ <b>Фото удалено.</b>", "parse_mode": "HTML"})
            elif text.startswith("http"):
                update_settings(cur, welcome_photo_url=text)
                send_telegram_api("sendMessage", {"chat_id": chat_id, "text": "✅ <b>Ссылка сохранена!</b>", "parse_mode": "HTML"})
            else:
                send_telegram_api("sendMessage", {"chat_id": chat_id, "text": "⚠️ Не распознано. Попробуйте ещё раз или /cancel"})
            return 200, {"ok": True}

        if admin_state == "waiting_sub_price":
            digits = "".join(c for c in text if c.isdigit())
            if digits and int(digits) > 0:
                update_settings(cur, subscription_price_rub=int(digits))
                cur.execute("delete from admin_chat_states where chat_id = %s", (chat_id,))
                send_telegram_api("sendMessage", {
                    "chat_id": chat_id, "text": f"✅ <b>Цена изменена на {digits}₽!</b>", "parse_mode": "HTML",
                })
                return 200, {"ok": True}
            send_telegram_api("sendMessage", {"chat_id": chat_id, "text": "⚠️ Введите число (например 490) или /cancel"})
            return 200, {"ok": True}

    settings = get_settings(cur)
    reply_text, reply_markup, is_photo_message = "", None, False

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        ref_param = parts[1].strip() if len(parts) > 1 else ""
        ref_code = ref_param[2:] if ref_param.startswith("c_") else \
                   ref_param[4:] if ref_param.startswith("ref_") else ref_param

        if ref_code:
            cur.execute("select * from contests where ref_code = %s", (ref_code,))
            contest = cur.fetchone()
            contest_url = f"{base_miniapp}/?ref={ref_code}"
            if contest:
                cur.execute("select * from prize_places where contest_id = %s order by place_number", (contest["id"],))
                c_places = cur.fetchall()
                places_str = ""
                if c_places:
                    lines = [f"• {p['place_number']} место: {p['prize_text']}" for p in c_places]
                    places_str = "\n🏆 <b>Призовые места:</b>\n" + "\n".join(lines) + "\n"
                desc_str = f"<i>{contest['description']}</i>\n" if contest["description"] else ""
                reply_text = (f"🎉 <b>Здравствуйте, {first_name}!</b>\n\n"
                              f"Вас пригласили принять участие в розыгрыше: <b>«{contest['title']}»</b>!\n\n"
                              f"{desc_str}{places_str}\n📋 Выполните условия чек-листа, чтобы занять призовое место!")
            else:
                reply_text = (f"🎁 <b>Здравствуйте, {first_name}!</b>\n\nВы приглашены к участию в розыгрыше призов!")
            reply_markup = {"inline_keyboard": [[{"text": "🎯 Открыть условия и участвовать", "web_app": {"url": contest_url}}]]}
        else:
            buttons = [[{"text": "🎁 Открыть конкурсы", "web_app": {"url": f"{base_miniapp}/"}}]]
            if str(user_id) == str(ADMIN_ID):
                buttons.append([{"text": "⚙️ Конструктор конкурсов (Admin)", "web_app": {"url": f"{base_miniapp}/?admin=1"}}])
                buttons.append([{"text": "🛠️ Настройки бота", "callback_data": "adm_settings"}])
            template = settings["welcome_text"] or "👋 Привет, {first_name}!"
            reply_text = template.replace("{first_name}", first_name)
            reply_markup = {"inline_keyboard": buttons}
            if settings["welcome_photo_url"]:
                is_photo_message = True

    elif text.startswith("/admin"):
        if str(user_id) == str(ADMIN_ID):
            has_photo = "Установлено ✅" if settings["welcome_photo_url"] else "Не установлено ❌"
            reply_text = (f"⚙️ <b>Панель администратора</b>\n\n💵 Цена подписки: {settings['subscription_price_rub']}₽ / 30 дней\n"
                          f"🖼️ Фото приветствия: {has_photo}\n\nРедактируйте здесь или откройте конструктор:")
            reply_markup = {"inline_keyboard": [
                [{"text": "✏️ Изменить текст приветствия", "callback_data": "adm_edit_text"}],
                [{"text": "🖼️ Изменить фото приветствия", "callback_data": "adm_edit_photo"}],
                [{"text": "💰 Изменить цену подписки", "callback_data": "adm_edit_price"}],
                [{"text": "📊 Открыть админ-панель конкурсов", "web_app": {"url": f"{base_miniapp}/?admin=1"}}],
            ]}
        else:
            reply_text = "⛔ Данная команда доступна только администратору бота."

    elif photo:
        # Look up which (participant, condition) this user last clicked
        # "Выполнить" for in the Mini App checklist. Without this we have no
        # way to know who should review the photo — this lookup + forward is
        # the fix for "screenshots never reach the admin".
        cur.execute("select * from pending_screenshots where user_id = %s", (user_id,))
        pending = cur.fetchone()

        if not pending:
            reply_text = (
                "📸 <b>Скриншот получен!</b>\n\nНо я не понял, к какому условию он относится — "
                "откройте чек-лист конкурса, нажмите «Выполнить» на нужном пункте и пришлите фото ещё раз."
            )
            reply_markup = {"inline_keyboard": [[{"text": "🔍 Открыть чек-лист", "web_app": {"url": f"{base_miniapp}/"}}]]}
        else:
            file_id = photo[-1]["file_id"]
            cur.execute(
                """update condition_checks set file_id = %s, status = 'pending', reviewed_at = null
                   where participant_id = %s and condition_id = %s
                   returning id""",
                (file_id, pending["participant_id"], pending["condition_id"]),
            )
            check_row = cur.fetchone()
            cur.execute("delete from pending_screenshots where user_id = %s", (user_id,))

            cur.execute("select * from contests where id = %s", (pending["contest_id"],))
            contest = cur.fetchone()
            cur.execute("select * from conditions where id = %s", (pending["condition_id"],))
            condition = cur.fetchone()

            if check_row and contest and condition:
                organizer_id = contest["owner_user_id"]
                username_part = f"@{from_user['username']}" if from_user.get("username") else first_name
                caption = (
                    f"🕵️ <b>Новый скриншот на проверку</b>\n\n"
                    f"Конкурс: <b>{contest['title']}</b>\n"
                    f"Условие: {condition['description']}\n"
                    f"Участник: {username_part} (id {user_id})"
                )
                send_telegram_api("sendPhoto", {
                    "chat_id": organizer_id,
                    "photo": file_id,
                    "caption": caption,
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": [[
                        {"text": "✅ Подтвердить", "callback_data": f"scr_ok:{check_row['id']}"},
                        {"text": "❌ Отклонить", "callback_data": f"scr_no:{check_row['id']}"},
                    ]]},
                })

            reply_text = (
                "📸 <b>Скриншот отправлен организатору на проверку!</b>\n\n"
                "Статус обновится в чек-листе, как только его проверят."
            )
            reply_markup = {"inline_keyboard": [[{"text": "🔍 Открыть чек-лист", "web_app": {"url": f"{base_miniapp}/"}}]]}

    else:
        reply_text = "👋 Чтобы принять участие в конкурсе или управлять розыгрышами, откройте приложение:"
        reply_markup = {"inline_keyboard": [[{"text": "🚀 Открыть приложение", "web_app": {"url": f"{base_miniapp}/"}}]]}

    if chat_id:
        if is_photo_message and settings["welcome_photo_url"]:
            send_telegram_api("sendPhoto", {
                "chat_id": chat_id, "photo": settings["welcome_photo_url"], "caption": reply_text,
                "parse_mode": "HTML", "reply_markup": reply_markup,
            })
        else:
            send_telegram_api("sendMessage", {
                "chat_id": chat_id, "text": reply_text, "parse_mode": "HTML", "reply_markup": reply_markup,
            })

    return 200, {"ok": True}


# ---------------------------------------------------------------------------
# Vercel entrypoints
# ---------------------------------------------------------------------------
def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    query_string = environ.get("QUERY_STRING", "")
    content_length = int(environ.get("CONTENT_LENGTH") or 0)
    body_bytes = environ["wsgi.input"].read(content_length) if content_length > 0 else b""
    headers = {
        "authorization": environ.get("HTTP_AUTHORIZATION", ""),
        "content-type": environ.get("CONTENT_TYPE", ""),
        "x-forwarded-host": environ.get("HTTP_X_FORWARDED_HOST", ""),
        "x-forwarded-proto": environ.get("HTTP_X_FORWARDED_PROTO", ""),
        "host": environ.get("HTTP_HOST", ""),
        "x-telegram-bot-api-secret-token": environ.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", ""),
    }

    status_code, result = process_api_request(method, query_string, body_bytes, headers)
    body = json.dumps(result).encode("utf-8")
    status_str = f"{status_code} {'OK' if status_code < 400 else 'Error'}"
    response_headers = [
        ("Content-Type", "application/json"),
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type, Authorization"),
        ("Content-Length", str(len(body))),
    ]
    start_response(status_str, response_headers)
    return [body]


class handler(BaseHTTPRequestHandler):
    def _handle_all(self, method: str):
        parsed = urlparse(self.path)
        length = int(self.headers.get("content-length", 0))
        body_bytes = self.rfile.read(length) if length > 0 else b""
        headers = {
            "authorization": self.headers.get("authorization", ""),
            "content-type": self.headers.get("content-type", ""),
            "x-forwarded-host": self.headers.get("x-forwarded-host", ""),
            "x-forwarded-proto": self.headers.get("x-forwarded-proto", ""),
            "host": self.headers.get("host", ""),
            "x-telegram-bot-api-secret-token": self.headers.get("x-telegram-bot-api-secret-token", ""),
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
