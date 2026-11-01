"""Sécurité et idempotence des actions Telegram par entreprise."""

import asyncio
import logging
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.modules.actions import company_service
from app.modules.auth.models import User
from app.modules.companies.models import Company
from app.modules.gmail.service import (
    GmailDraftNotFoundError,
    GmailTokenRevokedError,
)
from app.modules.gmail_oauth import service as gmail_oauth_service
from app.modules.gmail_oauth.models import GmailConnection
from app.modules.processed_emails.models import ProcessedEmail
from app.modules.telegram.models import TelegramConnection


class CompanyTelegramActionTests(unittest.TestCase):
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
        self.tenant_a = self._create_tenant("A", 10101, 20101, "A" * 32)
        self.tenant_b = self._create_tenant("B", 10102, 20102, "B" * 32)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _create_tenant(
        self,
        label: str,
        chat_id: int,
        telegram_user_id: int,
        callback_token: str,
    ) -> dict:
        with self.session_factory() as db:
            user = User(
                email=f"tenant-{label.lower()}@test.invalid",
                hashed_password="unused",
            )
            db.add(user)
            db.flush()
            company = Company(
                user_id=user.id,
                nom_entreprise=f"Tenant {label}",
                secteur="Test",
                sheet_id=f"sheet-{label}",
            )
            db.add(company)
            db.flush()
            db.add_all(
                [
                    GmailConnection(
                        user_id=user.id,
                        encrypted_refresh_token=f"encrypted-{label}",
                        status="connected",
                    ),
                    TelegramConnection(
                        user_id=user.id,
                        telegram_chat_id=chat_id,
                        telegram_user_id=telegram_user_id,
                    ),
                ]
            )
            record = ProcessedEmail(
                company_id=company.id,
                gmail_message_id=f"message-{label}",
                gmail_draft_id=f"draft-{label}",
                callback_token=callback_token,
                draft_status="pending",
                status="done",
                attempts=1,
                notified_at=datetime.now(UTC),
            )
            db.add(record)
            db.commit()
            return {
                "user_id": user.id,
                "company_id": company.id,
                "record_id": record.id,
                "chat_id": chat_id,
                "telegram_user_id": telegram_user_id,
                "token": callback_token,
            }

    @staticmethod
    def callback(action: str, tenant: dict, *, token: str | None = None) -> dict:
        return {
            "id": f"callback-{action}",
            "data": company_service.build_company_callback_data(
                action,
                token or tenant["token"],
            ),
            "from": {"id": tenant["telegram_user_id"]},
            "message": {"chat": {"id": tenant["chat_id"]}},
        }

    def test_tenant_b_cannot_view_send_or_discard_tenant_a_draft(self):
        async def exercise():
            for action in ("view", "send", "discard"):
                query = self.callback(
                    action, self.tenant_b, token=self.tenant_a["token"]
                )
                with self.session_factory() as db:
                    await company_service.handle_company_callback_query(query, db)

        with (
            patch.object(
                company_service,
                "answer_company_callback",
                new_callable=AsyncMock,
            ) as answer,
            patch.object(
                company_service,
                "send_company_telegram_message",
                new_callable=AsyncMock,
            ) as send_message,
            patch.object(
                company_service,
                "send_company_gmail_draft",
                new_callable=AsyncMock,
            ) as send_gmail,
            patch.object(company_service, "get_company_workbook") as workbook,
        ):
            asyncio.run(exercise())

        self.assertEqual(answer.await_count, 3)
        self.assertTrue(
            all(
                call.args[1] == "Action indisponible."
                for call in answer.await_args_list
            )
        )
        send_message.assert_not_awaited()
        send_gmail.assert_not_awaited()
        workbook.assert_not_called()
        with self.session_factory() as db:
            record = db.get(ProcessedEmail, self.tenant_a["record_id"])
        self.assertEqual(record.draft_status, "pending")

    def test_unknown_chat_unknown_token_and_missing_sheet_draft_are_generic(self):
        unknown_chat = self.callback("view", self.tenant_a)
        unknown_chat["message"]["chat"]["id"] = 99999
        unknown_token = self.callback("view", self.tenant_a, token="Z" * 32)
        valid = self.callback("view", self.tenant_a)

        async def exercise():
            for query in (unknown_chat, unknown_token, valid):
                with self.session_factory() as db:
                    await company_service.handle_company_callback_query(query, db)

        with (
            patch.object(
                company_service,
                "answer_company_callback",
                new_callable=AsyncMock,
            ) as answer,
            patch.object(company_service, "get_company_workbook", return_value="wb"),
            patch.object(
                company_service, "get_company_draft_content", return_value=None
            ),
        ):
            asyncio.run(exercise())

        self.assertEqual(answer.await_args_list[0].args[1], "Action indisponible.")
        self.assertEqual(answer.await_args_list[1].args[1], "Action indisponible.")
        self.assertEqual(answer.await_args_list[2].args[1], "Brouillon introuvable.")

    def test_view_is_truncated_and_exposes_only_opaque_action_tokens(self):
        query = self.callback("view", self.tenant_a)
        with (
            patch.object(
                company_service,
                "answer_company_callback",
                new_callable=AsyncMock,
            ),
            patch.object(
                company_service,
                "send_company_telegram_message",
                new_callable=AsyncMock,
            ) as send_message,
            patch.object(company_service, "get_company_workbook", return_value="wb"),
            patch.object(
                company_service,
                "get_company_draft_content",
                return_value="x" * 5000,
            ),
        ):
            with self.session_factory() as db:
                asyncio.run(company_service.handle_company_callback_query(query, db))

        text = send_message.await_args.args[1]
        markup = send_message.await_args.kwargs["reply_markup"]
        self.assertLessEqual(len(text), 4096)
        callback_values = [
            button["callback_data"] for button in markup["inline_keyboard"][0]
        ]
        self.assertTrue(all(value.startswith("mt_") for value in callback_values))
        self.assertTrue(all(len(value.encode()) <= 64 for value in callback_values))
        self.assertTrue(all("draft-A" not in value for value in callback_values))

    def test_send_double_click_and_replay_send_gmail_only_once(self):
        query = self.callback("send", self.tenant_a)

        async def exercise():
            for _ in range(3):
                with self.session_factory() as db:
                    await company_service.handle_company_callback_query(query, db)

        with (
            patch.object(
                company_service,
                "answer_company_callback",
                new_callable=AsyncMock,
            ) as answer,
            patch.object(
                company_service,
                "send_company_telegram_message",
                new_callable=AsyncMock,
            ),
            patch.object(company_service, "decrypt_token", return_value="refresh"),
            patch.object(
                company_service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                company_service,
                "send_company_gmail_draft",
                new_callable=AsyncMock,
            ) as send_gmail,
            patch.object(company_service, "get_company_workbook", return_value="wb"),
            patch.object(
                company_service, "update_company_draft_status"
            ) as update_sheet,
        ):
            asyncio.run(exercise())

        send_gmail.assert_awaited_once_with("access", "draft-A")
        update_sheet.assert_called_once()
        self.assertIn("déjà été envoyé", answer.await_args_list[-1].args[1])
        with self.session_factory() as db:
            record = db.get(ProcessedEmail, self.tenant_a["record_id"])
        self.assertEqual(record.draft_status, "sent")
        self.assertIsNotNone(record.sheet_status_synced_at)

    def test_discard_double_click_is_idempotent_and_never_calls_gmail(self):
        query = self.callback("discard", self.tenant_a)

        async def exercise():
            for _ in range(2):
                with self.session_factory() as db:
                    await company_service.handle_company_callback_query(query, db)

        with (
            patch.object(
                company_service,
                "answer_company_callback",
                new_callable=AsyncMock,
            ) as answer,
            patch.object(
                company_service,
                "send_company_gmail_draft",
                new_callable=AsyncMock,
            ) as send_gmail,
            patch.object(company_service, "get_company_workbook", return_value="wb"),
            patch.object(
                company_service, "update_company_draft_status"
            ) as update_sheet,
        ):
            asyncio.run(exercise())

        send_gmail.assert_not_awaited()
        update_sheet.assert_called_once()
        self.assertIn("déjà été ignoré", answer.await_args_list[-1].args[1])
        with self.session_factory() as db:
            record = db.get(ProcessedEmail, self.tenant_a["record_id"])
        self.assertEqual(record.draft_status, "discarded")

    def test_gmail_404_during_send_is_finalized_as_sent(self):
        query = self.callback("send", self.tenant_a)
        with (
            patch.object(
                company_service,
                "answer_company_callback",
                new_callable=AsyncMock,
            ),
            patch.object(
                company_service,
                "send_company_telegram_message",
                new_callable=AsyncMock,
            ) as send_message,
            patch.object(company_service, "decrypt_token", return_value="refresh"),
            patch.object(
                company_service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                company_service,
                "send_company_gmail_draft",
                new=AsyncMock(
                    side_effect=GmailDraftNotFoundError(
                        "gmail_draft_missing",
                        status_code=404,
                    )
                ),
            ),
            patch.object(company_service, "get_company_workbook", return_value="wb"),
            patch.object(
                company_service, "update_company_draft_status"
            ) as update_sheet,
        ):
            with self.session_factory() as db:
                asyncio.run(company_service.handle_company_callback_query(query, db))

        self.assertIn("envoyé", send_message.await_args.args[1])
        update_sheet.assert_called_once()
        with self.session_factory() as db:
            record = db.get(ProcessedEmail, self.tenant_a["record_id"])
        self.assertEqual(record.draft_status, "sent")
        self.assertIsNotNone(record.sheet_status_synced_at)

    def test_stale_sending_checks_gmail_404_then_marks_sent_without_resend(self):
        class SimulatedProcessStop(BaseException):
            pass

        query = self.callback("send", self.tenant_a)
        with (
            patch.object(
                company_service,
                "answer_company_callback",
                new_callable=AsyncMock,
            ),
            patch.object(
                company_service,
                "send_company_telegram_message",
                new_callable=AsyncMock,
            ),
            patch.object(company_service, "decrypt_token", return_value="refresh"),
            patch.object(
                company_service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                company_service,
                "send_company_gmail_draft",
                new=AsyncMock(side_effect=SimulatedProcessStop()),
            ) as first_send,
        ):
            with self.session_factory() as db:
                with self.assertRaises(SimulatedProcessStop):
                    asyncio.run(
                        company_service.handle_company_callback_query(query, db)
                    )

        first_send.assert_awaited_once_with("access", "draft-A")
        with self.session_factory() as db:
            record = db.get(ProcessedEmail, self.tenant_a["record_id"])
            self.assertEqual(record.draft_status, "sending")

        with (
            patch.object(
                company_service,
                "answer_company_callback",
                new_callable=AsyncMock,
            ) as immediate_answer,
            patch.object(
                company_service,
                "company_gmail_draft_exists",
                new_callable=AsyncMock,
            ) as immediate_check,
            patch.object(
                company_service,
                "send_company_gmail_draft",
                new_callable=AsyncMock,
            ) as immediate_send,
        ):
            with self.session_factory() as db:
                asyncio.run(company_service.handle_company_callback_query(query, db))

        immediate_check.assert_not_awaited()
        immediate_send.assert_not_awaited()
        self.assertIn("déjà en cours", immediate_answer.await_args.args[1])

        with self.session_factory() as db:
            record = db.get(ProcessedEmail, self.tenant_a["record_id"])
            record.updated_at = datetime.now(UTC) - timedelta(seconds=901)
            db.commit()

        order = []

        async def check_draft(*_args):
            order.append("check")
            return False

        async def forbidden_send(*_args):
            order.append("send")
            raise AssertionError("an ambiguous stale state must not be resent")

        with (
            patch.object(
                company_service,
                "answer_company_callback",
                new_callable=AsyncMock,
            ) as answer,
            patch.object(
                company_service,
                "send_company_telegram_message",
                new_callable=AsyncMock,
            ),
            patch.object(company_service, "decrypt_token", return_value="refresh"),
            patch.object(
                company_service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                company_service,
                "company_gmail_draft_exists",
                side_effect=check_draft,
            ) as check_gmail,
            patch.object(
                company_service,
                "send_company_gmail_draft",
                side_effect=forbidden_send,
            ) as send_gmail,
            patch.object(company_service, "get_company_workbook", return_value="wb"),
            patch.object(
                company_service, "update_company_draft_status"
            ) as update_sheet,
        ):
            with self.session_factory() as db:
                asyncio.run(company_service.handle_company_callback_query(query, db))

        check_gmail.assert_awaited_once_with("access", "draft-A")
        send_gmail.assert_not_awaited()
        self.assertEqual(order, ["check"])
        self.assertIn("Vérification", answer.await_args.args[1])
        update_sheet.assert_called_once()
        with self.session_factory() as db:
            record = db.get(ProcessedEmail, self.tenant_a["record_id"])
        self.assertEqual(record.draft_status, "sent")
        self.assertIsNotNone(record.sheet_status_synced_at)

    def test_stale_sending_checks_existing_draft_before_one_resend(self):
        with self.session_factory() as db:
            record = db.get(ProcessedEmail, self.tenant_a["record_id"])
            record.draft_status = "sending"
            record.updated_at = datetime.now(UTC) - timedelta(seconds=901)
            db.commit()

        query = self.callback("send", self.tenant_a)
        order = []

        async def check_draft(*_args):
            order.append("check")
            return True

        async def send_draft(*_args):
            order.append("send")

        with (
            patch.object(
                company_service,
                "answer_company_callback",
                new_callable=AsyncMock,
            ),
            patch.object(
                company_service,
                "send_company_telegram_message",
                new_callable=AsyncMock,
            ),
            patch.object(company_service, "decrypt_token", return_value="refresh"),
            patch.object(
                company_service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                company_service,
                "company_gmail_draft_exists",
                side_effect=check_draft,
            ),
            patch.object(
                company_service,
                "send_company_gmail_draft",
                side_effect=send_draft,
            ),
            patch.object(company_service, "get_company_workbook", return_value="wb"),
            patch.object(company_service, "update_company_draft_status"),
        ):
            with self.session_factory() as db:
                asyncio.run(company_service.handle_company_callback_query(query, db))

        self.assertEqual(order, ["check", "send"])
        with self.session_factory() as db:
            record = db.get(ProcessedEmail, self.tenant_a["record_id"])
        self.assertEqual(record.draft_status, "sent")

    def test_action_revocation_notice_is_sent_once_until_reconnection(self):
        query = self.callback("send", self.tenant_a)

        async def exercise():
            for _ in range(2):
                with self.session_factory() as db:
                    await company_service.handle_company_callback_query(query, db)

        with (
            patch.object(
                company_service,
                "answer_company_callback",
                new_callable=AsyncMock,
            ),
            patch.object(company_service, "decrypt_token", return_value="refresh"),
            patch.object(
                company_service,
                "get_company_access_token",
                new=AsyncMock(
                    side_effect=GmailTokenRevokedError(
                        "gmail_token_revoked",
                        status_code=400,
                    )
                ),
            ) as get_token,
            patch.object(
                company_service,
                "send_company_gmail_revoked_notification",
                new_callable=AsyncMock,
            ) as revoked_notification,
            patch.object(
                company_service,
                "send_company_telegram_message",
                new_callable=AsyncMock,
            ) as regular_message,
        ):
            asyncio.run(exercise())

        self.assertEqual(get_token.await_count, 1)
        revoked_notification.assert_awaited_once_with(self.tenant_a["chat_id"])
        regular_message.assert_not_awaited()
        with self.session_factory() as db:
            connection = db.scalar(
                select(GmailConnection).where(
                    GmailConnection.user_id == self.tenant_a["user_id"]
                )
            )
            record = db.get(ProcessedEmail, self.tenant_a["record_id"])
        self.assertEqual(connection.status, "revoked")
        self.assertIsNotNone(connection.revocation_notified_at)
        self.assertEqual(record.draft_status, "pending")

    def test_failed_revocation_notice_does_not_set_notification_marker(self):
        query = self.callback("send", self.tenant_a)

        async def exercise():
            for _ in range(2):
                with self.session_factory() as db:
                    await company_service.handle_company_callback_query(query, db)

        with (
            patch.object(
                company_service,
                "answer_company_callback",
                new_callable=AsyncMock,
            ),
            patch.object(company_service, "decrypt_token", return_value="refresh"),
            patch.object(
                company_service,
                "get_company_access_token",
                new=AsyncMock(
                    side_effect=GmailTokenRevokedError(
                        "gmail_token_revoked",
                        status_code=400,
                    )
                ),
            ) as get_token,
            patch.object(
                company_service,
                "send_company_gmail_revoked_notification",
                new=AsyncMock(
                    side_effect=company_service.CompanyTelegramError(
                        "telegram_transport_error"
                    )
                ),
            ) as revoked_notification,
        ):
            asyncio.run(exercise())

        self.assertEqual(get_token.await_count, 1)
        revoked_notification.assert_awaited_once_with(self.tenant_a["chat_id"])
        with self.session_factory() as db:
            connection = db.scalar(
                select(GmailConnection).where(
                    GmailConnection.user_id == self.tenant_a["user_id"]
                )
            )
            record = db.get(ProcessedEmail, self.tenant_a["record_id"])
        self.assertEqual(connection.status, "revoked")
        self.assertIsNone(connection.revocation_notified_at)
        self.assertEqual(record.draft_status, "pending")

    def test_gmail_reconnection_resets_revocation_notification_marker(self):
        with self.session_factory() as db:
            connection = db.scalar(
                select(GmailConnection).where(
                    GmailConnection.user_id == self.tenant_a["user_id"]
                )
            )
            connection.status = "revoked"
            connection.revocation_notified_at = datetime.now(UTC)
            db.commit()

        with patch.object(
            gmail_oauth_service,
            "encrypt_token",
            return_value="new-encrypted-token",
        ):
            with self.session_factory() as db:
                gmail_oauth_service.save_refresh_token(
                    db,
                    self.tenant_a["user_id"],
                    "new-private-refresh-token",
                )

        with self.session_factory() as db:
            connection = db.scalar(
                select(GmailConnection).where(
                    GmailConnection.user_id == self.tenant_a["user_id"]
                )
            )
        self.assertEqual(connection.status, "connected")
        self.assertIsNone(connection.revocation_notified_at)
        self.assertEqual(connection.encrypted_refresh_token, "new-encrypted-token")

    def test_actions_emit_no_personal_data_in_logs(self):
        sensitive_values = [
            "private.sender@test.invalid",
            "private body",
            str(self.tenant_a["chat_id"]),
        ]
        handler = logging.Handler()
        records: list[logging.LogRecord] = []
        handler.emit = records.append  # type: ignore[method-assign]
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            with (
                patch.object(
                    company_service,
                    "answer_company_callback",
                    new_callable=AsyncMock,
                ),
                patch.object(
                    company_service, "get_company_workbook", return_value="wb"
                ),
                patch.object(
                    company_service,
                    "get_company_draft_content",
                    return_value="private body",
                ),
                patch.object(
                    company_service,
                    "send_company_telegram_message",
                    new_callable=AsyncMock,
                ),
            ):
                with self.session_factory() as db:
                    asyncio.run(
                        company_service.handle_company_callback_query(
                            self.callback("view", self.tenant_a),
                            db,
                        )
                    )
        finally:
            root.removeHandler(handler)
        log_text = "\n".join(record.getMessage() for record in records)
        for value in sensitive_values:
            self.assertNotIn(value, log_text)


if __name__ == "__main__":
    unittest.main()
