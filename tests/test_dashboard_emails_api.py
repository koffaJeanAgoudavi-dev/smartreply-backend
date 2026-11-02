"""Tests ciblés des transformations de la nouvelle API Emails."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.modules.dashboard.emails import (
    _normalize,
    _sender_parts,
    get_company_email,
    list_company_emails,
)


class DashboardEmailHelpersTests(unittest.TestCase):
    def test_sender_parser_separates_display_name_and_address(self):
        self.assertEqual(
            _sender_parts('"Jean Dupont" <jean@example.com>'),
            ("Jean Dupont", "jean@example.com"),
        )
        self.assertEqual(
            _sender_parts("jean@example.com"),
            ("jean", "jean@example.com"),
        )
        self.assertEqual(_sender_parts(""), ("Expéditeur", ""))

    def test_search_normalization_is_case_and_accent_insensitive(self):
        self.assertEqual(_normalize("ÉQUIPE Développement"), "equipe developpement")

    @patch("app.modules.dashboard.emails.get_company_workbook")
    @patch("app.modules.dashboard.emails.list_company_email_rows")
    def test_list_filters_to_tenant_and_done_or_skipped_records(
        self, list_rows, get_workbook
    ):
        company = SimpleNamespace(id=7, sheet_id="sheet-7")
        get_workbook.return_value = object()
        list_rows.return_value = [
            {
                "email_id": "EM-11",
                "sender": "Jean Dupont <jean@example.com>",
                "subject": "Demande de devis",
                "summary": "Résumé complet du besoin.",
                "category": "Devis",
                "priority": "Haute",
                "status": "Brouillon prêt",
            },
            {
                "email_id": "EM-12",
                "sender": "Autre <autre@example.com>",
                "subject": "Newsletter",
                "summary": "Résumé newsletter.",
                "category": "Newsletter",
                "priority": "Basse",
                "status": "Analysé",
            },
        ]
        db = MagicMock()
        db.execute.return_value.all.return_value = [
            SimpleNamespace(
                id=11,
                received_at=None,
                category="devis",
                priority="haute",
                status="done",
                draft_status="pending",
            )
        ]

        result = list_company_emails(
            db, company=company, page=1, page_size=25, search="JEAN",
            category="devis", priority="haute", status="Brouillon prêt",
        )

        self.assertEqual(result.pagination.total_items, 1)
        self.assertEqual(result.items[0].email_id, "EM-11")
        self.assertEqual(result.items[0].sender_name, "Jean Dupont")
        self.assertEqual(result.items[0].sender_email, "jean@example.com")
        self.assertEqual(result.items[0].summary, "Résumé complet du besoin.")
        statement = db.execute.call_args.args[0]
        self.assertIsNotNone(statement)

    @patch("app.modules.dashboard.emails.get_company_workbook")
    @patch("app.modules.dashboard.emails.list_company_email_rows")
    def test_detail_rejects_invalid_ids_and_missing_records(
        self, list_rows, get_workbook
    ):
        company = SimpleNamespace(id=7, sheet_id="sheet-7")
        db = MagicMock()
        self.assertIsNone(get_company_email(db, company=company, email_id="EM-0"))
        get_workbook.return_value = object()
        list_rows.return_value = []
        self.assertIsNone(get_company_email(db, company=company, email_id="EM-99"))


if __name__ == "__main__":
    unittest.main()
