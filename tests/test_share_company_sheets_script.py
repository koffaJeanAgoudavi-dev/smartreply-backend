"""Tests du script de rattrapage des permissions Drive."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.modules.companies.service import DriveOperationError
from scripts import share_company_sheets_with_service_account as script


class ShareExistingSheetsScriptTests(unittest.TestCase):
    def test_database_configuration_is_required(self):
        with (
            patch.object(script, "engine", None),
            redirect_stderr(io.StringIO()),
        ):
            result = script.share_existing_company_sheets()

        self.assertEqual(result, 2)

    def test_all_companies_are_processed_and_failures_return_nonzero(self):
        companies = [
            SimpleNamespace(id=1, sheet_id="sheet-one"),
            SimpleNamespace(id=2, sheet_id="sheet-two"),
            SimpleNamespace(id=3, sheet_id="sheet-three"),
        ]
        db = MagicMock()
        db.scalars.return_value.all.return_value = companies
        session_context = MagicMock()
        session_context.__enter__.return_value = db

        with (
            patch.object(script, "engine", object()),
            patch.object(script, "SessionLocal", return_value=session_context),
            patch.object(
                script,
                "ensure_service_account_writer",
                side_effect=[True, False, DriveOperationError("échec test")],
            ) as ensure_writer,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()),
        ):
            result = script.share_existing_company_sheets()

        self.assertEqual(result, 1)
        self.assertEqual(
            [call.args[0] for call in ensure_writer.call_args_list],
            ["sheet-one", "sheet-two", "sheet-three"],
        )
        self.assertIn("1 mise(s) à jour", stdout.getvalue())
        self.assertIn("1 déjà conforme(s)", stdout.getvalue())
        self.assertIn("1 échec(s)", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
