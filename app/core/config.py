import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "SmartReply Agent API"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_WEBHOOK_SECRET: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    # URL publique du service (ex : https://smartreply-backend.onrender.com)
    BASE_URL: str = os.getenv("BASE_URL", "")

    # Google Sheets
    GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")
    # JSON complet du compte de service (Render) ; local = fichier credentials.json
    GOOGLE_SERVICE_ACCOUNT_JSON: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

    # Analyse IA (Module 3) — Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # Gmail (Module 1) — compte unique, Étape 1
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REFRESH_TOKEN: str = os.getenv("GOOGLE_REFRESH_TOKEN", "")
    
    # Sheet Data Entreprise (Module 4)
    GOOGLE_DATA_SHEET_ID: str = os.getenv("GOOGLE_DATA_SHEET_ID", "")

    class Config:
        env_file = ".env"


settings = Settings()
