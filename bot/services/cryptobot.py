"""
Crypto Pay API — https://help.crypt.bot/crypto-pay-api
Используем фиатный инвойс (currency_type=fiat), CryptoBot сам конвертирует
рубли в нужную криптовалюту по курсу на момент оплаты.
"""
import aiohttp

from bot.config import CRYPTOBOT_TOKEN, CRYPTOBOT_API_URL


async def _call(method: str, payload: dict) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{CRYPTOBOT_API_URL}/{method}",
            headers={"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN},
            json=payload,
        ) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"CryptoBot API error: {data}")
            return data["result"]


async def create_invoice(amount_rub: int, payload: str, description: str) -> dict:
    """Создаёт инвойс в рублях (CryptoBot сконвертирует в крипту), возвращает pay_url и invoice_id."""
    return await _call(
        "createInvoice",
        {
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": str(amount_rub),
            "description": description,
            "payload": payload,   # произвольная строка, вернётся при проверке статуса
        },
    )


async def get_invoice_status(invoice_id: str) -> str | None:
    """Возвращает статус: 'active' | 'paid' | 'expired', либо None если не найден."""
    result = await _call("getInvoices", {"invoice_ids": invoice_id})
    items = result.get("items", [])
    return items[0]["status"] if items else None
