"""Tests des adaptateurs paramétrés sans altérer les wrappers historiques."""

import asyncio
import base64
import unittest
from unittest.mock import patch

from app.modules.drafts import service as drafts_service
from app.modules.gmail import service as gmail_service
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


if __name__ == "__main__":
    unittest.main()
