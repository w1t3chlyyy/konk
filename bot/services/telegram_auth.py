"""
Проверка подлинности initData, которую Mini App присылает на бэкенд.
Алгоритм из офиц. доков Telegram:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
from urllib.parse import parse_qsl

from bot.config import BOT_TOKEN


def validate_init_data(init_data: str) -> dict | None:
    """Возвращает распарсенный user dict, если подпись верна, иначе None."""
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            return None

        user_raw = pairs.get("user")
        return json.loads(user_raw) if user_raw else {}
    except Exception:
        return None
