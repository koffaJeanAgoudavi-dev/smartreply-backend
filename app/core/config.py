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

    # Google Sheets par entreprise
    # JSON complet du compte de service (Render) ; local = fichier credentials.json
    GOOGLE_SERVICE_ACCOUNT_JSON: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

    # Google Drive — compte propriétaire des copies de Sheets d'entreprise (OAuth)
    GOOGLE_DRIVE_CLIENT_ID: str = os.getenv("GOOGLE_DRIVE_CLIENT_ID", "")
    GOOGLE_DRIVE_CLIENT_SECRET: str = os.getenv("GOOGLE_DRIVE_CLIENT_SECRET", "")
    GOOGLE_DRIVE_REFRESH_TOKEN: str = os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN", "")
    GOOGLE_DRIVE_TEMPLATE_ID: str = os.getenv(
        "GOOGLE_DRIVE_TEMPLATE_ID",
        "1nmfyipaN32gsmesH6IdUAa0LqP0uG-zN6BNBy2WtUzE",
    )

    # Analyse IA (Module 3) — Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # Gmail OAuth par utilisateur
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")

    # PostgreSQL (Neon) — Étape 2 : couche multi-utilisateur (auth)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # Authentification (Étape 2) — invitation, signature et validité du JWT
    INVITATION_CODES: str = os.getenv("INVITATION_CODES", "")
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
        os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440")
    )  # 24 h

    # Protection anti-abus partagée. CLIENT_IP_HEADER doit uniquement nommer
    # un en-tête remplacé par la plateforme (Render public : CF-Connecting-IP).
    REDIS_URL: str = os.getenv("REDIS_URL", "")
    RATE_LIMIT_HMAC_SECRET: str = os.getenv("RATE_LIMIT_HMAC_SECRET", "")
    CLIENT_IP_HEADER: str = os.getenv("CLIENT_IP_HEADER", "")
    LOGIN_IP_EMAIL_RATE_LIMIT: int = int(
        os.getenv("LOGIN_IP_EMAIL_RATE_LIMIT", "5")
    )
    LOGIN_IP_RATE_LIMIT: int = int(os.getenv("LOGIN_IP_RATE_LIMIT", "20"))
    LOGIN_EMAIL_RATE_LIMIT: int = int(os.getenv("LOGIN_EMAIL_RATE_LIMIT", "5"))
    LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = int(
        os.getenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "900")
    )
    REGISTER_IP_RATE_LIMIT: int = int(os.getenv("REGISTER_IP_RATE_LIMIT", "5"))
    REGISTER_RATE_LIMIT_WINDOW_SECONDS: int = int(
        os.getenv("REGISTER_RATE_LIMIT_WINDOW_SECONDS", "3600")
    )

    # Gmail OAuth par utilisateur (Étape 3) — chiffrement des refresh tokens (Fernet)
    TOKEN_ENCRYPTION_KEY: str = os.getenv("TOKEN_ENCRYPTION_KEY", "")

    # Pipeline multi-entreprises
    MAX_EMAILS_PER_COMPANY_PER_RUN: int = int(
        os.getenv("MAX_EMAILS_PER_COMPANY_PER_RUN", "10")
    )
    PIPELINE_RUN_TIME_BUDGET_SECONDS: int = int(
        os.getenv("PIPELINE_RUN_TIME_BUDGET_SECONDS", "240")
    )
    # Liste CSV d'identifiants internes. Vide = simulation pour toutes les entreprises.
    PIPELINE_ALLOWED_COMPANY_IDS: str = os.getenv("PIPELINE_ALLOWED_COMPANY_IDS", "")
    GEMINI_MAX_EMAIL_BODY_CHARS: int = int(
        os.getenv("GEMINI_MAX_EMAIL_BODY_CHARS", "6000")
    )
    PIPELINE_PROCESSING_STALE_SECONDS: int = int(
        os.getenv("PIPELINE_PROCESSING_STALE_SECONDS", "900")
    )

    class Config:
        env_file = ".env"


settings = Settings()
