import json
import os
import urllib.parse

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "SmartReply Agent API"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_WEBHOOK_SECRET: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    # URL publique du service (ex : https://smartreply-backend.onrender.com)
    BASE_URL: str = os.getenv("BASE_URL", "")

    # Formulaire public de contact — livraison SMTP authentifiée
    CONTACT_RECIPIENT_EMAIL: str = os.getenv(
        "CONTACT_RECIPIENT_EMAIL",
        "hello.smartreplyagent@gmail.com",
    )
    CONTACT_SMTP_HOST: str = os.getenv("CONTACT_SMTP_HOST", "smtp.gmail.com")
    CONTACT_SMTP_PORT: int = int(os.getenv("CONTACT_SMTP_PORT", "465"))
    CONTACT_SMTP_USERNAME: str = os.getenv("CONTACT_SMTP_USERNAME", "")
    CONTACT_SMTP_PASSWORD: str = os.getenv("CONTACT_SMTP_PASSWORD", "")
    CONTACT_SMTP_TIMEOUT_SECONDS: int = int(
        os.getenv("CONTACT_SMTP_TIMEOUT_SECONDS", "15")
    )

    # Google Sheets par entreprise
    # JSON complet du compte de service (Render) ; local = fichier credentials.json
    GOOGLE_SERVICE_ACCOUNT_JSON: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

    # Google Drive — OAuth distinct par utilisateur (scope drive.file).
    GOOGLE_DRIVE_CLIENT_ID: str = os.getenv("GOOGLE_DRIVE_CLIENT_ID", "")
    GOOGLE_DRIVE_CLIENT_SECRET: str = os.getenv("GOOGLE_DRIVE_CLIENT_SECRET", "")
    # Compatibilité des anciens Sheets détenus par la plateforme uniquement.
    GOOGLE_DRIVE_REFRESH_TOKEN: str = os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN", "")

    # Analyse IA (Module 3) — Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

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
    CONTACT_IP_RATE_LIMIT: int = int(os.getenv("CONTACT_IP_RATE_LIMIT", "5"))
    CONTACT_RATE_LIMIT_WINDOW_SECONDS: int = int(
        os.getenv("CONTACT_RATE_LIMIT_WINDOW_SECONDS", "3600")
    )

    # Gmail OAuth par utilisateur (Étape 3) — chiffrement des refresh tokens (Fernet)
    TOKEN_ENCRYPTION_KEY: str = os.getenv("TOKEN_ENCRYPTION_KEY", "")

    # Pipeline multi-entreprises
    CRON_SECRET: str = os.getenv("CRON_SECRET", "")
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


class RuntimeConfigurationError(RuntimeError):
    """La configuration empêche un démarrage sûr en production."""


settings = Settings()


def production_configuration_errors(
    config: Settings = settings,
    *,
    environment: str | None = None,
) -> tuple[str, ...]:
    """Retourne uniquement les noms des réglages invalides, jamais leurs valeurs."""

    normalized_environment = (
        config.ENVIRONMENT if environment is None else environment
    ).strip().lower()
    if normalized_environment not in {"development", "test", "production"}:
        return ("ENVIRONMENT",)
    if normalized_environment != "production":
        return ()

    errors: set[str] = set()
    required_text_fields = (
        "BASE_URL",
        "CONTACT_SMTP_USERNAME",
        "CONTACT_SMTP_PASSWORD",
        "CRON_SECRET",
        "DATABASE_URL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_DRIVE_CLIENT_ID",
        "GOOGLE_DRIVE_CLIENT_SECRET",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "JWT_SECRET",
        "RATE_LIMIT_HMAC_SECRET",
        "CLIENT_IP_HEADER",
        "TOKEN_ENCRYPTION_KEY",
    )
    for field_name in required_text_fields:
        value = getattr(config, field_name, "")
        if not isinstance(value, str) or not value.strip():
            errors.add(field_name)

    parsed_base_url = urllib.parse.urlsplit(str(config.BASE_URL).strip())
    if (
        parsed_base_url.scheme != "https"
        or not parsed_base_url.hostname
        or parsed_base_url.username is not None
        or parsed_base_url.password is not None
        or parsed_base_url.path not in {"", "/"}
        or parsed_base_url.query
        or parsed_base_url.fragment
    ):
        errors.add("BASE_URL")

    database_url = str(config.DATABASE_URL).strip()
    database_scheme = urllib.parse.urlsplit(database_url).scheme
    if database_url and not (
        database_scheme in {"postgres", "postgresql"}
        or database_scheme.startswith("postgresql+")
    ):
        errors.add("DATABASE_URL")

    client_ip_header = str(config.CLIENT_IP_HEADER).strip()
    if any(character in client_ip_header for character in "\r\n,:"):
        errors.add("CLIENT_IP_HEADER")

    try:
        service_account = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
    except (json.JSONDecodeError, TypeError):
        errors.add("GOOGLE_SERVICE_ACCOUNT_JSON")
    else:
        required_service_account_fields = ("client_email", "private_key", "token_uri")
        if not isinstance(service_account, dict) or any(
            not isinstance(service_account.get(field_name), str)
            or not service_account[field_name].strip()
            for field_name in required_service_account_fields
        ):
            errors.add("GOOGLE_SERVICE_ACCOUNT_JSON")

    telegram_values = (
        bool(config.TELEGRAM_BOT_TOKEN.strip()),
        bool(config.TELEGRAM_WEBHOOK_SECRET.strip()),
    )
    if telegram_values[0] != telegram_values[1]:
        errors.update(("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET"))

    positive_integer_fields = (
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        "CONTACT_SMTP_PORT",
        "CONTACT_SMTP_TIMEOUT_SECONDS",
        "LOGIN_IP_EMAIL_RATE_LIMIT",
        "LOGIN_IP_RATE_LIMIT",
        "LOGIN_EMAIL_RATE_LIMIT",
        "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
        "REGISTER_IP_RATE_LIMIT",
        "REGISTER_RATE_LIMIT_WINDOW_SECONDS",
        "CONTACT_IP_RATE_LIMIT",
        "CONTACT_RATE_LIMIT_WINDOW_SECONDS",
        "MAX_EMAILS_PER_COMPANY_PER_RUN",
        "PIPELINE_RUN_TIME_BUDGET_SECONDS",
        "GEMINI_MAX_EMAIL_BODY_CHARS",
        "PIPELINE_PROCESSING_STALE_SECONDS",
    )
    for field_name in positive_integer_fields:
        value = getattr(config, field_name, None)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.add(field_name)

    raw_company_ids = config.PIPELINE_ALLOWED_COMPANY_IDS.strip()
    if raw_company_ids:
        candidates = (candidate.strip() for candidate in raw_company_ids.split(","))
        if any(
            not candidate.isdecimal() or int(candidate) <= 0
            for candidate in candidates
        ):
            errors.add("PIPELINE_ALLOWED_COMPANY_IDS")

    return tuple(sorted(errors))


def validate_runtime_configuration(
    config: Settings = settings,
    *,
    environment: str | None = None,
) -> None:
    """Bloque le démarrage de production si un réglage critique est invalide."""

    errors = production_configuration_errors(config, environment=environment)
    if errors:
        raise RuntimeConfigurationError(
            "Configuration d'exécution invalide : " + ", ".join(errors)
        )


# Cette vérification précède l'initialisation SQLAlchemy lors d'un vrai démarrage
# Render et empêche une configuration incomplète d'atteindre les modules métier.
validate_runtime_configuration(settings)
