"""Validation fail-fast de la configuration et disponibilité PostgreSQL."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.config import (
    RuntimeConfigurationError,
    production_configuration_errors,
    settings,
    validate_runtime_configuration,
)
from app.main import create_app


def valid_production_configuration() -> SimpleNamespace:
    return SimpleNamespace(
        ENVIRONMENT="production",
        BASE_URL="https://smartreply.example",
        CONTACT_SMTP_USERNAME="hello@smartreply.example",
        CONTACT_SMTP_PASSWORD="smtp-password",
        CONTACT_SMTP_PORT=465,
        CONTACT_SMTP_TIMEOUT_SECONDS=15,
        CRON_SECRET="cron-secret",
        DATABASE_URL="postgresql://user:password@database.example/smartreply",
        GEMINI_API_KEY="gemini-key",
        GEMINI_MODEL="gemini-2.5-flash",
        GOOGLE_CLIENT_ID="gmail-client",
        GOOGLE_CLIENT_SECRET="gmail-secret",
        GOOGLE_DRIVE_CLIENT_ID="drive-client",
        GOOGLE_DRIVE_CLIENT_SECRET="drive-secret",
        GOOGLE_SERVICE_ACCOUNT_JSON=json.dumps(
            {
                "client_email": "pipeline@example.iam.gserviceaccount.com",
                "private_key": "private-key",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        JWT_SECRET="jwt-secret",
        RATE_LIMIT_HMAC_SECRET="rate-limit-secret",
        CLIENT_IP_HEADER="CF-Connecting-IP",
        TOKEN_ENCRYPTION_KEY="token-encryption-secret",
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_WEBHOOK_SECRET="",
        ACCESS_TOKEN_EXPIRE_MINUTES=1440,
        LOGIN_IP_EMAIL_RATE_LIMIT=5,
        LOGIN_IP_RATE_LIMIT=20,
        LOGIN_EMAIL_RATE_LIMIT=5,
        LOGIN_RATE_LIMIT_WINDOW_SECONDS=900,
        REGISTER_IP_RATE_LIMIT=5,
        REGISTER_RATE_LIMIT_WINDOW_SECONDS=3600,
        CONTACT_IP_RATE_LIMIT=5,
        CONTACT_RATE_LIMIT_WINDOW_SECONDS=3600,
        MAX_EMAILS_PER_COMPANY_PER_RUN=10,
        PIPELINE_RUN_TIME_BUDGET_SECONDS=240,
        GEMINI_MAX_EMAIL_BODY_CHARS=6000,
        PIPELINE_PROCESSING_STALE_SECONDS=900,
        PIPELINE_ALLOWED_COMPANY_IDS="1,2",
        ADMIN_ALLOWED_USER_IDS="1",
    )


class RuntimeConfigurationTests(unittest.TestCase):
    def test_valid_production_configuration_is_accepted(self):
        config = valid_production_configuration()

        self.assertEqual(production_configuration_errors(config), ())
        validate_runtime_configuration(config)

    def test_invalid_production_configuration_lists_names_without_values(self):
        values = vars(valid_production_configuration()).copy()
        values.update(
            {
                "BASE_URL": "http://user:password@smartreply.example/path?secret=value",
                "CLIENT_IP_HEADER": "X-Forwarded-For,CF-Connecting-IP",
                "GOOGLE_SERVICE_ACCOUNT_JSON": '{"private_key":"do-not-log"}',
                "TELEGRAM_BOT_TOKEN": "bot-token",
                "TELEGRAM_WEBHOOK_SECRET": "",
                "PIPELINE_RUN_TIME_BUDGET_SECONDS": 0,
                "PIPELINE_ALLOWED_COMPANY_IDS": "1,invalid",
                "ADMIN_ALLOWED_USER_IDS": "1,invalid",
            }
        )
        config = SimpleNamespace(**values)

        with self.assertRaises(RuntimeConfigurationError) as raised:
            validate_runtime_configuration(config)

        message = str(raised.exception)
        for field_name in (
            "BASE_URL",
            "CLIENT_IP_HEADER",
            "GOOGLE_SERVICE_ACCOUNT_JSON",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_WEBHOOK_SECRET",
            "PIPELINE_RUN_TIME_BUDGET_SECONDS",
            "PIPELINE_ALLOWED_COMPANY_IDS",
            "ADMIN_ALLOWED_USER_IDS",
        ):
            self.assertIn(field_name, message)
        for secret_value in ("password", "secret=value", "do-not-log", "bot-token"):
            self.assertNotIn(secret_value, message)

    def test_non_production_configuration_does_not_require_remote_services(self):
        config = SimpleNamespace(ENVIRONMENT="development")

        self.assertEqual(production_configuration_errors(config), ())
        validate_runtime_configuration(config)

    def test_unknown_environment_is_rejected_instead_of_exposing_debug_docs(self):
        config = SimpleNamespace(ENVIRONMENT="prodution")

        self.assertEqual(production_configuration_errors(config), ("ENVIRONMENT",))
        with self.assertRaises(RuntimeConfigurationError):
            validate_runtime_configuration(config)

    def test_real_production_app_fails_fast_when_required_settings_are_missing(self):
        with (
            patch.object(settings, "ENVIRONMENT", "production"),
            patch.object(settings, "BASE_URL", ""),
            self.assertRaises(RuntimeConfigurationError),
        ):
            create_app()

    def test_readiness_requires_a_live_database_connection(self):
        unavailable_app = create_app("development")
        with (
            patch("app.main.engine", None),
            TestClient(unavailable_app) as client,
        ):
            unavailable = client.get("/ready")

        connection = MagicMock()
        ready_engine = MagicMock()
        ready_engine.connect.return_value.__enter__.return_value = connection
        ready_app = create_app("development")
        with (
            patch("app.main.engine", ready_engine),
            TestClient(ready_app) as client,
        ):
            ready = client.get("/ready")

        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json(), {"status": "not_ready"})
        self.assertEqual(unavailable.headers["Cache-Control"], "no-store")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json(), {"status": "ready"})
        self.assertEqual(ready.headers["Cache-Control"], "no-store")
        connection.execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
