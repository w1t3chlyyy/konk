import os

# Telegram Bot Token
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Telegram ID of the Super Admin
try:
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "123456789"))
except (ValueError, TypeError):
    ADMIN_ID = 123456789

# Database URL (PostgreSQL / Supabase)
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Secret for Cron requests
CRON_SECRET = os.environ.get("CRON_SECRET", "")

# Mini App Base URL (with auto-detection from Vercel system environment variables)
vercel_url = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or os.environ.get("VERCEL_URL")
default_miniapp_url = f"https://{vercel_url}" if vercel_url else "http://localhost:3000"
MINIAPP_URL = os.environ.get("MINIAPP_URL") or default_miniapp_url

# CryptoBot Pay API token
CRYPTOBOT_TOKEN = os.environ.get("CRYPTOBOT_TOKEN", "")
