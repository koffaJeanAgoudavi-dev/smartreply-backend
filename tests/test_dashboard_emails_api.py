"""Tests de l'API Emails, de son cloisonnement et de sa compatibilité Sheet."""

import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.database import Base, get_db
from app.main import app
from app.modules.auth.models import User
from app.modules.auth.security import create_access_token
from app.modules.companies.models import Company
from app.modules.dashboard import emails as email_service
from app.modules.dashboard import service as dashboard_service
from app.modules.processed_emails.models import ProcessedEmail
from app.modules.sheets.service import (
    EMAIL_HEADERS,
    list_company_email_rows_with_summary,
)


class EmailWorksheet:
    def __init__(self, rows):
        self.rows = [list(row) for row in rows]
        self.requested_ranges = []

    def get(self, cell_range):
        self.requested_ranges.append(cell_range)
        if cell_range == "A:C":
            return [list(row[:3]) for row in self.rows]
        if cell_range == "E:G":
            return [list(row[4:7]) for row in self.rows]
        if cell_range == "A:G":
            return [list(row[:7]) for row in self.rows]
        raise AssertionError(f"unexpected Sheet range: {cell_range}")


class EmailWorkbook:
    def __init__(self, rows):
        self.email_sheet = EmailWorksheet(rows)

    def worksheet(self, name):
        if name != "Emails":
            raise AssertionError(f"unexpected worksheet: {name}")
        return self.email_sheet


class DashboardEmailHelpersTests(unittest.TestCase):
    def test_sender_parser_separates_display_name_and_address(self):
        self.assertEqual(
            email_service._sender_parts('"Jean Dupont" <jean@example.test>'),
            ("Jean Dupont", "jean@example.test"),
        )
        self.assertEqual(
            email_service._sender_parts("jean@example.test"),
            ("jean", "jean@example.test"),
        )
        self.assertEqual(
            email_service._sender_parts(""),
            ("Expéditeur", ""),
        )

    def test_search_normalization_is_case_accent_and_space_insensitive(self):
        self.assertEqual(
            email_service._normalize("  ÉQUIPE   Développement  "),
            "equipe developpement",
        )

    def test_summary_reader_uses_a_dedicated_range(self):
        workbook = EmailWorkbook(
            [
                EMAIL_HEADERS,
                [
                    "EM-1",
                    "Equipe <equipe@example.test>",
                    "Objet",
                    "Résumé complet",
                    "devis",
                    "haute",
                    "Analysé",
                    "draft-not-read",
                ],
            ]
        )

        rows = list_company_email_rows_with_summary(workbook)

        self.assertEqual(workbook.email_sheet.requested_ranges, ["A:G"])
        self.assertEqual(rows[0]["summary"], "Résumé complet")
        self.assertNotIn("draft_id", rows[0])


class DashboardEmailsApiTests(unittest.TestCase):
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
        settings.JWT_SECRET = "dashboard-emails-api-tests-secret-32-bytes"

        with self.session_factory() as db:
            user_a = User(email="owner-a@test.invalid", hashed_password="unused")
            user_b = User(email="owner-b@test.invalid", hashed_password="unused")
            db.add_all([user_a, user_b])
            db.flush()
            company_a = Company(
                user_id=user_a.id,
                nom_entreprise="Tenant A",
                secteur="Test",
                sheet_id="sheet-a-emails",
            )
            company_b = Company(
                user_id=user_b.id,
                nom_entreprise="Tenant B",
                secteur="Test",
                sheet_id="sheet-b-emails",
            )
            db.add_all([company_a, company_b])
            db.flush()
            db.add_all(
                [
                    self._record(
                        101,
                        company_a.id,
                        "done",
                        datetime(2026, 1, 3, 12, tzinfo=UTC),
                        category="devis",
                        priority="haute",
                        draft_status="pending",
                    ),
                    self._record(
                        102,
                        company_a.id,
                        "skipped",
                        datetime(2026, 1, 2, 12, tzinfo=UTC),
                    ),
                    self._record(
                        103,
                        company_a.id,
                        "processing",
                        datetime(2026, 1, 5, 12, tzinfo=UTC),
                    ),
                    self._record(
                        104,
                        company_a.id,
                        "done",
                        datetime(2026, 1, 3, 12, tzinfo=UTC),
                        category="support",
                        priority="moyenne",
                        draft_status="sent",
                    ),
                    self._record(
                        105,
                        company_a.id,
                        "failed",
                        datetime(2026, 1, 6, 12, tzinfo=UTC),
                    ),
                    self._record(
                        106,
                        company_a.id,
                        "excluded",
                        datetime(2026, 1, 7, 12, tzinfo=UTC),
                    ),
                    self._record(
                        201,
                        company_b.id,
                        "done",
                        datetime(2026, 1, 8, 12, tzinfo=UTC),
                        category="autre",
                        priority="basse",
                    ),
                ]
            )
            db.commit()
            self.user_a_id = user_a.id
            self.user_b_id = user_b.id

        self.rows_a = [
            EMAIL_HEADERS,
            self._row(
                101,
                "Ancien <ancien@example.test>",
                "Ancien objet",
                "Ancien résumé du doublon",
                "devis",
                "haute",
                "Analysé",
            ),
            self._row(
                102,
                "Robot <robot@example.test>",
                "Message automatique",
                "Email ignoré enregistré.",
                "",
                "",
                "Ignoré",
            ),
            self._row(
                103,
                "Traitement <processing@example.test>",
                "En cours",
                "Ne doit pas être exposé.",
                "support",
                "haute",
                "Analysé",
            ),
            self._row(
                105,
                "Erreur <failed@example.test>",
                "Échec",
                "Ne doit pas être exposé.",
                "autre",
                "basse",
                "Analysé",
            ),
            self._row(
                106,
                "Exclu <excluded@example.test>",
                "Exclu",
                "Ne doit pas être exposé.",
                "autre",
                "basse",
                "Ignoré",
            ),
            self._row(
                201,
                "Tenant B <foreign@example.test>",
                "Email étranger",
                "Ne doit jamais traverser le tenant.",
                "autre",
                "basse",
                "Analysé",
            ),
            self._row(
                104,
                "Support <support@example.test>",
                "Assistance technique",
                "Résumé support.",
                "support",
                "moyenne",
                "Envoyé",
                "draft-104",
            ),
            self._row(
                101,
                "Équipe Développement <equipe@example.test>",
                "Réunion stratégie",
                "Résumé complet le plus récent.",
                "devis",
                "haute",
                "Brouillon prêt",
                "draft-101",
            ),
        ]
        self.rows_b = [
            EMAIL_HEADERS,
            self._row(
                201,
                "Tenant B <tenant-b@example.test>",
                "Demande B",
                "Résumé du tenant B.",
                "autre",
                "basse",
                "Analysé",
            ),
        ]
        self.workbooks = {
            "sheet-a-emails": EmailWorkbook(self.rows_a),
            "sheet-b-emails": EmailWorkbook(self.rows_b),
        }
        self.token_a = create_access_token(self.user_a_id, "owner-a@test.invalid")
        self.token_b = create_access_token(self.user_b_id, "owner-b@test.invalid")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        settings.JWT_SECRET = self.original_jwt_secret
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def _record(
        record_id,
        company_id,
        record_status,
        received_at,
        *,
        category=None,
        priority=None,
        draft_status=None,
    ):
        return ProcessedEmail(
            id=record_id,
            company_id=company_id,
            gmail_message_id=f"private-gmail-{record_id}",
            status=record_status,
            received_at=received_at,
            category=category,
            priority=priority,
            draft_status=draft_status,
        )

    @staticmethod
    def _row(
        record_id,
        sender,
        subject,
        summary,
        category,
        priority,
        email_status,
        draft_id="",
    ):
        return [
            f"EM-{record_id}",
            sender,
            subject,
            summary,
            category,
            priority,
            email_status,
            draft_id,
        ]

    @staticmethod
    def _auth(token):
        return {"Authorization": f"Bearer {token}"}

    def _workbook(self, sheet_id):
        return self.workbooks[sheet_id]

    def test_list_is_tenant_scoped_final_deduplicated_sorted_and_paginated(self):
        with patch.object(
            email_service,
            "get_company_workbook",
            side_effect=self._workbook,
        ):
            first = self.client.get(
                "/api/v1/dashboard/emails?page=1&page_size=2",
                headers=self._auth(self.token_a),
            )
            second = self.client.get(
                "/api/v1/dashboard/emails?page=2&page_size=2",
                headers=self._auth(self.token_a),
            )
            empty = self.client.get(
                "/api/v1/dashboard/emails?page=3&page_size=2",
                headers=self._auth(self.token_a),
            )
            tenant_b = self.client.get(
                "/api/v1/dashboard/emails",
                headers=self._auth(self.token_b),
            )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.headers["Cache-Control"], "no-store")
        self.assertEqual(
            first.json()["pagination"],
            {"page": 1, "page_size": 2, "total_items": 3, "total_pages": 2},
        )
        self.assertEqual(
            [item["email_id"] for item in first.json()["items"]],
            ["EM-104", "EM-101"],
        )
        self.assertEqual(
            first.json()["items"][1]["summary"],
            "Résumé complet le plus récent.",
        )
        self.assertEqual(
            set(first.json()["items"][0]),
            {
                "email_id",
                "sender_name",
                "sender_email",
                "subject",
                "received_at",
                "summary",
                "category",
                "priority",
                "status",
            },
        )
        serialized = first.text + second.text + empty.text
        for forbidden_value in (
            "EM-103",
            "EM-105",
            "EM-106",
            "EM-201",
            "private-gmail",
            "company_id",
            "gmail_message_id",
            "body",
            "corps",
        ):
            self.assertNotIn(forbidden_value, serialized)

        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(
            [item["email_id"] for item in second.json()["items"]],
            ["EM-102"],
        )
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["items"], [])
        self.assertEqual(empty.json()["pagination"]["total_pages"], 2)

        self.assertEqual(tenant_b.status_code, 200, tenant_b.text)
        self.assertEqual(
            [item["email_id"] for item in tenant_b.json()["items"]],
            ["EM-201"],
        )

    def test_search_and_all_filters_can_be_combined(self):
        base_params = {
            "category": "DEVIS",
            "priority": "HAUTE",
            "status": "brouillon pret",
        }
        with patch.object(
            email_service,
            "get_company_workbook",
            side_effect=self._workbook,
        ):
            for search in (
                "EQUIPE developpement",
                "equipe@example.test",
                "strategie",
            ):
                with self.subTest(search=search):
                    response = self.client.get(
                        "/api/v1/dashboard/emails",
                        params={**base_params, "search": search},
                        headers=self._auth(self.token_a),
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(
                        [item["email_id"] for item in response.json()["items"]],
                        ["EM-101"],
                    )
                    self.assertEqual(
                        response.json()["pagination"]["total_items"],
                        1,
                    )

    def test_detail_returns_own_final_email_and_hides_all_other_cases(self):
        with patch.object(
            email_service,
            "get_company_workbook",
            side_effect=self._workbook,
        ):
            own = self.client.get(
                "/api/v1/dashboard/emails/EM-101",
                headers=self._auth(self.token_a),
            )
            refused_ids = (
                "EM-0",
                "not-an-email",
                "EM-103",
                "EM-105",
                "EM-106",
                "EM-201",
                "EM-999",
            )
            refused = [
                self.client.get(
                    f"/api/v1/dashboard/emails/{email_id}",
                    headers=self._auth(self.token_a),
                )
                for email_id in refused_ids
            ]

        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual(own.json()["email_id"], "EM-101")
        self.assertEqual(
            own.json()["summary"],
            "Résumé complet le plus récent.",
        )
        self.assertTrue(all(response.status_code == 404 for response in refused))
        self.assertTrue(
            all(
                response.json() == {"detail": "Email introuvable."}
                for response in refused
            )
        )

    def test_sheet_errors_are_safe_for_list_detail_and_invalid_headers(self):
        private_diagnostic = "private-sheet-owner@example.test"
        with patch.object(
            email_service,
            "get_company_workbook",
            side_effect=RuntimeError(private_diagnostic),
        ):
            list_error = self.client.get(
                "/api/v1/dashboard/emails",
                headers=self._auth(self.token_a),
            )
            detail_error = self.client.get(
                "/api/v1/dashboard/emails/EM-101",
                headers=self._auth(self.token_a),
            )

        invalid_workbook = EmailWorkbook([["wrong-header"]])
        with patch.object(
            email_service,
            "get_company_workbook",
            return_value=invalid_workbook,
        ):
            header_error = self.client.get(
                "/api/v1/dashboard/emails",
                headers=self._auth(self.token_a),
            )

        for response in (list_error, detail_error, header_error):
            self.assertEqual(response.status_code, 502, response.text)
            self.assertEqual(
                response.json(),
                {"detail": "Les emails sont temporairement indisponibles."},
            )
            self.assertNotIn(private_diagnostic, response.text)

    def test_authentication_and_query_bounds_are_enforced(self):
        unauthenticated = self.client.get("/api/v1/dashboard/emails")
        self.assertEqual(unauthenticated.status_code, 401, unauthenticated.text)

        for params in (
            {"page": 0},
            {"page_size": 0},
            {"page_size": 101},
            {"search": "x" * 201},
            {"category": "x" * 65},
            {"priority": "x" * 33},
            {"status": "x" * 33},
        ):
            with self.subTest(params=params):
                response = self.client.get(
                    "/api/v1/dashboard/emails",
                    params=params,
                    headers=self._auth(self.token_a),
                )
                self.assertEqual(response.status_code, 422, response.text)

    def test_recent_emails_keeps_its_original_sheet_ranges_and_public_schema(self):
        workbook = EmailWorkbook(self.rows_a)
        with patch.object(
            dashboard_service,
            "get_company_workbook",
            return_value=workbook,
        ):
            response = self.client.get(
                "/api/v1/dashboard/recent-emails",
                headers=self._auth(self.token_a),
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            workbook.email_sheet.requested_ranges,
            ["A:C", "E:G"],
        )
        self.assertNotIn("summary", response.text)
        self.assertNotIn("Résumé complet le plus récent.", response.text)
        for item in response.json()["emails"]:
            self.assertEqual(
                set(item),
                {
                    "sender",
                    "subject",
                    "received_at",
                    "category",
                    "priority",
                    "status",
                },
            )


if __name__ == "__main__":
    unittest.main()
