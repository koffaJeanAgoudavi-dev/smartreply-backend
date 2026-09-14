import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "SmartReply Agent API"
    ENVIRONMENT: str = "development"
    
    # Telegram Configuration
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_WEBHOOK_SECRET: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    
    # Google Sheets Configuration
    GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")

    class Config:
        env_file = ".env"

settings = Settings()
