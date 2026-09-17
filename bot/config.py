import os

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])           # твой Telegram user_id — единственный с доступом к админке
DATABASE_URL = os.environ["DATABASE_URL"]        # connection string из Supabase (Transaction pooler)
CRON_SECRET = os.environ.get("CRON_SECRET", "")  # чтобы cron-эндпоинт не мог дёрнуть кто попало

MINIAPP_URL = os.environ["MINIAPP_URL"]          # https://<домен>.vercel.app — база для Mini App (статика)

CRYPTOBOT_TOKEN = os.environ["CRYPTOBOT_TOKEN"]  # токен приложения из @CryptoBot -> Crypto Pay -> Create App
CRYPTOBOT_API_URL = "https://pay.crypt.bot/api"
SUBSCRIPTION_PRICE_RUB = 390
SUBSCRIPTION_DAYS = 30
