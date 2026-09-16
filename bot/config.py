import os

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])           # твой Telegram user_id — только у тебя доступ к /admin
DATABASE_URL = os.environ["DATABASE_URL"]        # connection string из Supabase (Settings -> Database)
CRON_SECRET = os.environ.get("CRON_SECRET", "")  # чтобы cron-эндпоинт не мог дёрнуть кто попало
