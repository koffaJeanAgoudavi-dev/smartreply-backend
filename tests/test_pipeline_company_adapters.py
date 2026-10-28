"""Tests des adaptateurs paramétrés sans altérer les wrappers historiques."""

import asyncio
import base64
import logging
import unittest
from unittest.mock import AsyncMock, patch

from app.modules.drafts import service as drafts_service
from app.modules.gmail import service as gmail_service
from app.modules.notifications import service as notifications_service
from app.modules.sheets import service as sheets_service


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeAsyncClient:
    def __init__(self, *, get_response=None, post_response=None):
        self.get_response = get_response
        self.post_response = post_response
        self.get_calls = []
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_response

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_response


class CompanyGmailAdapterTests(unittest.TestCase):
    def test_list_uses_explicit_token_and_epoch_cutoff(self):
        client = FakeAsyncClient(
            get_response=FakeResponse(
                200,
                {
                    "messages": [{"id": "internal-message"}],
                    "nextPageToken": "next-page",
                },
            )
        )
        with patch.object(gmail_service.httpx, "AsyncClient", return_value=client):
            message_ids, next_page = asyncio.run(
                gmail_service.list_company_message_page(
                    "company-access-token",
                    after_epoch_seconds=1_798_000_123,
                    page_token="current-page",
                    page_size=25,
                )
            )

        self.assertEqual(message_ids, ("internal-message",))
        self.assertEqual(next_page, "next-page")
        _, kwargs = client.get_calls[0]
        self.assertEqual(
            kwargs["headers"],
            {"Authorization": "Bearer company-access-token"},
        )
        self.assertEqual(kwargs["params"]["q"], "in:inbox after:1798000123")
        self.assertEqual(kwargs["params"]["pageToken"], "current-page")

    def test_invalid_grant_raises_only_a_safe_operational_code(self):
        client = FakeAsyncClient(
            post_response=FakeResponse(
                400,
                {
                    "error": "invalid_grant",
                    "error_description": "private provider detail",
                },
            )
        )
        with patch.object(gmail_service.httpx, "AsyncClient", return_value=client):
            with self.assertRaises(gmail_service.GmailTokenRevokedError) as caught:
                asyncio.run(
                    gmail_service.get_company_access_token("private-refresh-token")
                )

        self.assertEqual(str(caught.exception), "gmail_token_revoked")
        self.assertNotIn("private provider", str(caught.exception))
        _, kwargs = client.post_calls[0]
        self.assertEqual(kwargs["data"]["refresh_token"], "private-refresh-token")

    def test_draft_uses_explicit_token_recipient_and_thread(self):
        client = FakeAsyncClient(
            post_response=FakeResponse(200, {"id": "draft-internal-id"})
        )
        with patch.object(drafts_service.httpx, "AsyncClient", return_value=client):
            draft_id = asyncio.run(
                drafts_service.create_company_gmail_draft(
                    "company-access-token",
                    destinataire="recipient@test.invalid",
                    objet="Re: Request",
                    corps="Draft body",
                    thread_id="company-thread",
                    in_reply_to="<message@test.invalid>",
                )
            )

        self.assertEqual(draft_id, "draft-internal-id")
        _, kwargs = client.post_calls[0]
        self.assertEqual(
            kwargs["headers"],
            {"Authorization": "Bearer company-access-token"},
        )
        self.assertEqual(kwargs["json"]["message"]["threadId"], "company-thread")
        raw = base64.urlsafe_b64decode(kwargs["json"]["message"]["raw"]).decode()
        self.assertIn("To: recipient@test.invalid", raw)
        self.assertIn("In-Reply-To: <message@test.invalid>", raw)


class CompanyNotificationAdapterTests(unittest.TestCase):
    def test_notification_contains_summary_and_only_an_opaque_view_button(self):
        client = FakeAsyncClient(
            post_response=FakeResponse(200, {"ok": True, "result": {}})
        )
        original_token = notifications_service.settings.TELEGRAM_BOT_TOKEN
        notifications_service.settings.TELEGRAM_BOT_TOKEN = "configured-test-token"
        try:
            with patch.object(
                notifications_service.httpx,
                "AsyncClient",
                return_value=client,
            ):
                asyncio.run(
                    notifications_service.send_company_email_notification(
                        12345,
                        expediteur="Sender <sender@test.invalid>",
                        objet="Request",
                        categorie="support",
                        priorite="moyenne",
                        resume="Summary",
                        view_callback_data="mt_view_" + "A" * 32,
                    )
                )
        finally:
            notifications_service.settings.TELEGRAM_BOT_TOKEN = original_token

        _, kwargs = client.post_calls[0]
        payload = kwargs["json"]
        self.assertIn("Sender", payload["text"])
        self.assertIn("Request", payload["text"])
        buttons = payload["reply_markup"]["inline_keyboard"]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(len(buttons[0]), 1)
        self.assertEqual(buttons[0][0]["callback_data"], "mt_view_" + "A" * 32)
        self.assertLessEqual(len(buttons[0][0]["callback_data"].encode()), 64)

    def test_http_403_is_classified_as_permanently_blocked(self):
        client = FakeAsyncClient(post_response=FakeResponse(403, {}))
        original_token = notifications_service.settings.TELEGRAM_BOT_TOKEN
        notifications_service.settings.TELEGRAM_BOT_TOKEN = "configured-test-token"
        try:
            with patch.object(
                notifications_service.httpx,
                "AsyncClient",
                return_value=client,
            ):
                with self.assertRaises(
                    notifications_service.CompanyTelegramBlockedError
                ):
                    asyncio.run(
                        notifications_service.send_company_telegram_message(
                            12345,
                            "Test",
                        )
                    )
        finally:
            notifications_service.settings.TELEGRAM_BOT_TOKEN = original_token

    def test_bot_token_is_redacted_from_http_client_logs(self):
        original_token = notifications_service.settings.TELEGRAM_BOT_TOKEN
        notifications_service.settings.TELEGRAM_BOT_TOKEN = "private-bot-token"
        try:
            record = logging.LogRecord(
                name="httpx",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg="HTTP Request: %s",
                args=(
                    "https://api.telegram.org/botprivate-bot-token/sendMessage",
                ),
                exc_info=None,
            )
            redaction_filter = notifications_service._TelegramTokenRedactionFilter()
            self.assertTrue(redaction_filter.filter(record))
            self.assertNotIn("private-bot-token", record.getMessage())
            self.assertIn("[REDACTED]", record.getMessage())
        finally:
            notifications_service.settings.TELEGRAM_BOT_TOKEN = original_token


class HistoricalWrapperDelegationTests(unittest.TestCase):
    def test_gmail_wrappers_delegate_with_legacy_cache_and_shape(self):
        token_core = AsyncMock(return_value="historical-access")
        with patch.object(
            gmail_service,
            "_get_access_token_for_refresh_token",
            token_core,
        ):
            token = asyncio.run(gmail_service._get_access_token())
        self.assertEqual(token, "historical-access")
        token_core.assert_awaited_once_with(
            gmail_service.settings.GOOGLE_REFRESH_TOKEN,
            token_cache=gmail_service._token_cache,
            legacy_errors=True,
        )

        list_core = AsyncMock(return_value=[{"id_gmail": "message"}])
        with (
            patch.object(
                gmail_service,
                "_get_access_token",
                new=AsyncMock(return_value="historical-access"),
            ),
            patch.object(
                gmail_service,
                "_list_unread_emails_with_access_token",
                list_core,
            ),
        ):
            emails = asyncio.run(gmail_service.list_unread_emails(max_results=7))
        self.assertEqual(emails, [{"id_gmail": "message"}])
        list_core.assert_awaited_once_with("historical-access", max_results=7)

    def test_draft_wrappers_delegate_without_adding_a_thread(self):
        draft_core = AsyncMock(return_value="historical-draft")
        with (
            patch.object(
                drafts_service,
                "_get_access_token",
                new=AsyncMock(return_value="historical-access"),
            ),
            patch.object(
                drafts_service,
                "_create_gmail_draft_with_token",
                draft_core,
            ),
        ):
            draft_id = asyncio.run(
                drafts_service.create_gmail_draft(
                    "recipient@test.invalid",
                    "Subject",
                    "Body",
                )
            )
        self.assertEqual(draft_id, "historical-draft")
        draft_core.assert_awaited_once_with(
            "historical-access",
            destinataire="recipient@test.invalid",
            objet="Subject",
            corps="Body",
            thread_id=None,
            legacy_errors=True,
        )

        with (
            patch.object(
                drafts_service,
                "get_data_workbook",
                return_value="historical-workbook",
            ),
            patch.object(
                drafts_service,
                "append_draft_to_workbook",
                return_value=4,
            ) as append_core,
        ):
            row = drafts_service.record_draft_in_sheet("draft", "email", "Body")
        self.assertEqual(row, 4)
        append_core.assert_called_once_with(
            "historical-workbook",
            brouillon_id="draft",
            email_id="email",
            contenu="Body",
        )

    def test_sheet_wrappers_delegate_with_historical_append_parameters(self):
        with patch.object(
            sheets_service,
            "_get_workbook_by_id",
            return_value="historical-workbook",
        ) as workbook_core:
            workbook = sheets_service.get_data_workbook()
        self.assertEqual(workbook, "historical-workbook")
        workbook_core.assert_called_once_with(
            sheets_service.settings.GOOGLE_DATA_SHEET_ID,
            require_nonempty_id=False,
        )

        with (
            patch.object(
                sheets_service,
                "get_data_workbook",
                return_value="historical-workbook",
            ),
            patch.object(
                sheets_service,
                "append_analyzed_email_to_workbook",
                return_value={"email_id": "EM-fixed", "ligne": 3},
            ) as append_core,
        ):
            result = sheets_service.append_analyzed_email(
                "sender@test.invalid",
                "Subject",
                "Summary",
                "support",
                "moyenne",
                email_id="EM-fixed",
            )
        self.assertEqual(result, {"email_id": "EM-fixed", "ligne": 3})
        append_core.assert_called_once_with(
            "historical-workbook",
            expediteur="sender@test.invalid",
            objet="Subject",
            resume="Summary",
            categorie="support",
            priorite="moyenne",
            statut="Analysé",
            brouillon_id="",
            email_id="EM-fixed",
        )


class FakeWorksheet:
    def __init__(self, rows):
        self.rows = [list(row) for row in rows]
        self.append_calls = []

    def get_all_values(self):
        return [list(row) for row in self.rows]

    def append_row(self, values):
        self.append_calls.append(list(values))
        self.rows.append(list(values))

    def update(self, *, range_name, values):
        row_number = int(range_name.split(":", 1)[0][1:])
        self.rows[row_number - 1] = list(values[0])

    def update_cell(self, row, column, value):
        while len(self.rows[row - 1]) < column:
            self.rows[row - 1].append("")
        self.rows[row - 1][column - 1] = value


class FakeWorkbook:
    def __init__(self):
        self.sheets = {
            "Emails": FakeWorksheet(
                [
                    [
                        "Email_ID",
                        "Expéditeur",
                        "Objet",
                        "Résumé",
                        "Catégorie",
                        "Priorité",
                        "Statut",
                        "Brouillon_ID",
                    ]
                ]
            ),
            "Brouillons": FakeWorksheet(
                [["Brouillon_ID", "Email_ID", "Contenu", "Statut"]]
            ),
        }

    def worksheet(self, name):
        return self.sheets[name]


class CompanySheetAdapterTests(unittest.TestCase):
    def test_double_write_is_idempotent_by_company_email_id(self):
        workbook = FakeWorkbook()
        common = {
            "email_id": "EM-42",
            "expediteur": "sender@test.invalid",
            "objet": "Request",
            "resume": "Summary",
            "categorie": "support",
            "priorite": "moyenne",
            "statut": "Analysé",
        }
        sheets_service.upsert_company_email(workbook, **common)
        sheets_service.upsert_company_email(workbook, **common)
        sheets_service.upsert_company_draft(
            workbook,
            draft_id="draft-42",
            email_id="EM-42",
            contenu="Draft",
        )
        sheets_service.upsert_company_draft(
            workbook,
            draft_id="draft-42",
            email_id="EM-42",
            contenu="Draft",
        )
        sheets_service.mark_company_email_draft_ready(
            workbook,
            email_id="EM-42",
            draft_id="draft-42",
        )

        email_rows = workbook.sheets["Emails"].rows
        draft_rows = workbook.sheets["Brouillons"].rows
        self.assertEqual(len(email_rows), 2)
        self.assertEqual(len(draft_rows), 2)
        self.assertEqual(email_rows[1][6], "Brouillon prêt")
        self.assertEqual(email_rows[1][7], "draft-42")
        self.assertEqual(draft_rows[1][3], "En attente")

    def test_company_action_reads_and_updates_only_explicit_workbook(self):
        workbook = FakeWorkbook()
        sheets_service.upsert_company_email(
            workbook,
            email_id="EM-84",
            expediteur="sender@test.invalid",
            objet="Request",
            resume="Summary",
            categorie="support",
            priorite="moyenne",
            statut="Analysé",
        )
        sheets_service.upsert_company_draft(
            workbook,
            draft_id="draft-84",
            email_id="EM-84",
            contenu="Draft content",
        )

        notification = sheets_service.get_company_notification_data(
            workbook,
            email_id="EM-84",
        )
        content = sheets_service.get_company_draft_content(
            workbook,
            email_id="EM-84",
            draft_id="draft-84",
        )
        sheets_service.update_company_draft_status(
            workbook,
            email_id="EM-84",
            draft_id="draft-84",
            statut="Envoyé",
        )

        self.assertEqual(notification["objet"], "Request")
        self.assertEqual(content, "Draft content")
        self.assertEqual(workbook.sheets["Emails"].rows[1][6], "Envoyé")
        self.assertEqual(workbook.sheets["Brouillons"].rows[1][3], "Envoyé")


if __name__ == "__main__":
    unittest.main()
