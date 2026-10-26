"""Tests du flux de liaison Telegram stocké dans Neon."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.database import Base, get_db
from app.main import app
from app.modules.auth.models import User
from app.modules.auth.security import create_access_token
from app.modules.telegram import service
from app.modules.telegram.models import TelegramConnection, TelegramLinkCode


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        Base.metadata.create_all(self.engine)

        def override_get_db():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.original_jwt_secret = settings.JWT_SECRET
        self.original_webhook_secret = settings.TELEGRAM_WEBHOOK_SECRET
        settings.JWT_SECRET = "telegram-tests-secret-at-least-32-bytes"
        settings.TELEGRAM_WEBHOOK_SECRET = "telegram-webhook-test-secret"

        with self.session_factory() as db:
            first_user = User(
                email="telegram-one@example.com",
                hashed_password="not-used",
            )
            second_user = User(
                email="telegram-two@example.com",
                hashed_password="not-used",
            )
            db.add_all([first_user, second_user])
            db.commit()
            db.refresh(first_user)
            db.refresh(second_user)
            self.first_user_id = first_user.id
            self.second_user_id = second_user.id

        self.first_token = create_access_token(
            self.first_user_id, "telegram-one@example.com"
        )
        self.second_token = create_access_token(
            self.second_user_id, "telegram-two@example.com"
        )
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        settings.JWT_SECRET = self.original_jwt_secret
        settings.TELEGRAM_WEBHOOK_SECRET = self.original_webhook_secret
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def authorization(self, token: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {token or self.first_token}"}

    @property
    def webhook_headers(self) -> dict[str, str]:
        return {"X-Telegram-Bot-Api-Secret-Token": (settings.TELEGRAM_WEBHOOK_SECRET)}

    @staticmethod
    def message_update(
        text: str,
        *,
        chat_id: int = 10001,
        telegram_user_id: int = 20001,
    ) -> dict:
        return {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": telegram_user_id},
                "text": text,
            },
        }

    def create_code(
        self,
        user_id: int,
        code: str,
        *,
        expires_at: datetime | None = None,
    ) -> TelegramLinkCode:
        with self.session_factory() as db:
            link_code = TelegramLinkCode(
                user_id=user_id,
                code=code,
                status="pending",
                expires_at=expires_at
                or datetime.now(timezone.utc) + timedelta(minutes=15),
            )
            db.add(link_code)
            db.commit()
            db.refresh(link_code)
            return link_code

    def test_secure_code_format_uses_five_random_digits(self):
        with patch.object(service.secrets, "choice", return_value="7") as choice:
            code = service._generate_link_code()
        self.assertEqual(code, "SR-77777")
        self.assertEqual(choice.call_count, 5)

    def test_link_code_endpoint_creates_a_15_minute_pending_code(self):
        before = datetime.now(timezone.utc)
        with patch.object(
            service,
            "_generate_link_code",
            return_value="SR-12345",
        ):
            response = self.client.post(
                "/api/v1/telegram/link-code",
                headers=self.authorization(),
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["code"], "SR-12345")
        self.assertIn("/start SR-12345", payload["instruction"])
        expiration = datetime.fromisoformat(payload["expires_at"])
        self.assertGreaterEqual(expiration, before + timedelta(minutes=14, seconds=59))
        self.assertLessEqual(expiration, before + timedelta(minutes=15, seconds=2))

        with self.session_factory() as db:
            stored = db.scalar(
                select(TelegramLinkCode).where(TelegramLinkCode.code == "SR-12345")
            )
            self.assertIsNotNone(stored)
            self.assertEqual(stored.user_id, self.first_user_id)
            self.assertEqual(stored.status, "pending")

    def test_new_code_expires_the_previous_pending_code(self):
        with patch.object(
            service,
            "_generate_link_code",
            side_effect=["SR-11111", "SR-22222"],
        ):
            first = self.client.post(
                "/api/v1/telegram/link-code",
                headers=self.authorization(),
            )
            second = self.client.post(
                "/api/v1/telegram/link-code",
                headers=self.authorization(),
            )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        with self.session_factory() as db:
            codes = {
                row.code: row.status
                for row in db.scalars(select(TelegramLinkCode)).all()
            }
        self.assertEqual(codes, {"SR-11111": "expired", "SR-22222": "pending"})

    def test_jwt_endpoints_require_authentication(self):
        link_code_response = self.client.post("/api/v1/telegram/link-code")
        status_response = self.client.get("/api/v1/telegram/status")
        self.assertEqual(link_code_response.status_code, 401, link_code_response.text)
        self.assertEqual(status_response.status_code, 401, status_response.text)

    def test_connected_user_cannot_generate_a_reconnection_code(self):
        with self.session_factory() as db:
            db.add(
                TelegramConnection(
                    user_id=self.first_user_id,
                    telegram_chat_id=30001,
                    telegram_user_id=40001,
                )
            )
            db.commit()

        response = self.client.post(
            "/api/v1/telegram/link-code",
            headers=self.authorization(),
        )
        self.assertEqual(response.status_code, 409, response.text)

    def test_status_endpoint_never_returns_telegram_identifiers(self):
        disconnected = self.client.get(
            "/api/v1/telegram/status",
            headers=self.authorization(),
        )
        self.assertEqual(
            disconnected.json(),
            {"connected": False, "connected_at": None},
        )

        with self.session_factory() as db:
            db.add(
                TelegramConnection(
                    user_id=self.first_user_id,
                    telegram_chat_id=30001,
                    telegram_user_id=40001,
                )
            )
            db.commit()

        connected = self.client.get(
            "/api/v1/telegram/status",
            headers=self.authorization(),
        )
        self.assertEqual(connected.status_code, 200, connected.text)
        self.assertTrue(connected.json()["connected"])
        self.assertIsNotNone(connected.json()["connected_at"])
        self.assertNotIn("chat", connected.text.lower())
        self.assertNotIn("telegram_user_id", connected.text)

    def test_complete_api_flow_uses_neon_without_legacy_sheets(self):
        with (
            patch.object(
                service,
                "get_central_workbook",
                side_effect=AssertionError("legacy Sheet must not be used"),
            ),
            patch.object(
                service,
                "_generate_link_code",
                return_value="SR-23456",
            ),
            patch.object(
                service,
                "send_telegram_message",
                new_callable=AsyncMock,
            ) as send_message,
        ):
            code_response = self.client.post(
                "/api/v1/telegram/link-code",
                headers=self.authorization(),
            )
            webhook_response = self.client.post(
                "/api/v1/telegram/webhook",
                headers=self.webhook_headers,
                json=self.message_update(
                    f"/start {code_response.json()['code']}",
                ),
            )
            status_response = self.client.get(
                "/api/v1/telegram/status",
                headers=self.authorization(),
            )

        self.assertEqual(code_response.status_code, 200, code_response.text)
        self.assertEqual(webhook_response.status_code, 200, webhook_response.text)
        self.assertEqual(
            set(status_response.json()),
            {"connected", "connected_at"},
        )
        self.assertTrue(status_response.json()["connected"])
        self.assertIsNotNone(status_response.json()["connected_at"])
        self.assertIn("Connexion réussie", send_message.await_args.args[1])

    def test_webhook_rejects_an_invalid_secret(self):
        response = self.client.post(
            "/api/v1/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json=self.message_update("/help"),
        )
        self.assertEqual(response.status_code, 401, response.text)

    def test_start_code_atomically_creates_connection_and_marks_code_used(self):
        self.create_code(self.first_user_id, "SR-33333")
        with patch.object(
            service,
            "send_telegram_message",
            new_callable=AsyncMock,
        ) as send_message:
            response = self.client.post(
                "/api/v1/telegram/webhook",
                headers=self.webhook_headers,
                json=self.message_update("/start SR-33333"),
            )

        self.assertEqual(response.status_code, 200, response.text)
        with self.session_factory() as db:
            stored_code = db.scalar(
                select(TelegramLinkCode).where(TelegramLinkCode.code == "SR-33333")
            )
            connection = db.scalar(select(TelegramConnection))
            self.assertEqual(stored_code.status, "used")
            self.assertIsNotNone(stored_code.used_at)
            self.assertEqual(connection.user_id, self.first_user_id)
            self.assertEqual(connection.telegram_chat_id, 10001)
            self.assertEqual(connection.telegram_user_id, 20001)
        self.assertIn("Connexion réussie", send_message.await_args.args[1])

    def test_code_sent_without_start_is_accepted(self):
        self.create_code(self.first_user_id, "SR-44444")
        with patch.object(
            service,
            "send_telegram_message",
            new_callable=AsyncMock,
        ) as send_message:
            response = self.client.post(
                "/api/v1/telegram/webhook",
                headers=self.webhook_headers,
                json=self.message_update("sr-44444"),
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Connexion réussie", send_message.await_args.args[1])

    def test_expired_code_creates_no_connection_and_is_marked_expired(self):
        self.create_code(
            self.first_user_id,
            "SR-55555",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        with patch.object(
            service,
            "send_telegram_message",
            new_callable=AsyncMock,
        ) as send_message:
            response = self.client.post(
                "/api/v1/telegram/webhook",
                headers=self.webhook_headers,
                json=self.message_update("/start SR-55555"),
            )

        self.assertEqual(response.status_code, 200, response.text)
        with self.session_factory() as db:
            stored_code = db.scalar(
                select(TelegramLinkCode).where(TelegramLinkCode.code == "SR-55555")
            )
            connection_count = db.scalar(
                select(func.count()).select_from(TelegramConnection)
            )
        self.assertEqual(stored_code.status, "expired")
        self.assertEqual(connection_count, 0)
        self.assertIn("Code expiré", send_message.await_args.args[1])

    def test_invalid_code_creates_no_connection(self):
        with patch.object(
            service,
            "send_telegram_message",
            new_callable=AsyncMock,
        ) as send_message:
            response = self.client.post(
                "/api/v1/telegram/webhook",
                headers=self.webhook_headers,
                json=self.message_update("SR-99999"),
            )

        self.assertEqual(response.status_code, 200, response.text)
        with self.session_factory() as db:
            connection_count = db.scalar(
                select(func.count()).select_from(TelegramConnection)
            )
        self.assertEqual(connection_count, 0)
        self.assertIn("Code invalide", send_message.await_args.args[1])

    def test_same_code_can_only_be_consumed_once(self):
        self.create_code(self.first_user_id, "SR-66666")
        with self.session_factory() as db:
            first_result = service.consume_link_code(
                db,
                "SR-66666",
                telegram_chat_id=60001,
                telegram_user_id=61001,
            )
        with self.session_factory() as db:
            second_result = service.consume_link_code(
                db,
                "SR-66666",
                telegram_chat_id=60002,
                telegram_user_id=61002,
            )
            connection_count = db.scalar(
                select(func.count()).select_from(TelegramConnection)
            )

        self.assertEqual(first_result, service.LinkCodeUseResult.SUCCESS)
        self.assertEqual(second_result, service.LinkCodeUseResult.USED)
        self.assertEqual(connection_count, 1)

    def test_chat_cannot_be_linked_to_two_users(self):
        self.create_code(self.first_user_id, "SR-70001")
        self.create_code(self.second_user_id, "SR-70002")
        with self.session_factory() as db:
            first_result = service.consume_link_code(
                db,
                "SR-70001",
                telegram_chat_id=70000,
                telegram_user_id=71001,
            )
        with self.session_factory() as db:
            second_result = service.consume_link_code(
                db,
                "SR-70002",
                telegram_chat_id=70000,
                telegram_user_id=71002,
            )
            second_code = db.scalar(
                select(TelegramLinkCode).where(TelegramLinkCode.code == "SR-70002")
            )

        self.assertEqual(first_result, service.LinkCodeUseResult.SUCCESS)
        self.assertEqual(
            second_result,
            service.LinkCodeUseResult.CHAT_ALREADY_CONNECTED,
        )
        self.assertEqual(second_code.status, "pending")

    def test_bot_status_reads_neon_and_never_the_legacy_sheet(self):
        with self.session_factory() as db:
            db.add(
                TelegramConnection(
                    user_id=self.first_user_id,
                    telegram_chat_id=80001,
                    telegram_user_id=81001,
                )
            )
            db.commit()

        with (
            patch.object(
                service,
                "get_central_workbook",
                side_effect=AssertionError("legacy Sheet must not be read"),
            ),
            patch.object(
                service,
                "send_telegram_message",
                new_callable=AsyncMock,
            ) as send_message,
        ):
            response = self.client.post(
                "/api/v1/telegram/webhook",
                headers=self.webhook_headers,
                json=self.message_update("/status", chat_id=80001),
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Connecté", send_message.await_args.args[1])

    def test_callbacks_are_forwarded_unchanged_to_actions(self):
        callback = {
            "id": "callback-id",
            "data": "ignore:email-id:draft-id",
            "message": {"chat": {"id": 90001}},
        }
        with patch(
            "app.modules.actions.service.handle_callback_query",
            new_callable=AsyncMock,
        ) as handle_callback:
            response = self.client.post(
                "/api/v1/telegram/webhook",
                headers=self.webhook_headers,
                json={"update_id": 2, "callback_query": callback},
            )

        self.assertEqual(response.status_code, 200, response.text)
        handle_callback.assert_awaited_once_with(callback)


if __name__ == "__main__":
    unittest.main()
