"""Tests de l'API Brouillons, de ses actions et de l'isolation tenant."""

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.database import Base, get_db
from app.main import app
from app.modules.auth.models import User
from app.modules.auth.security import create_access_token
from app.modules.companies.models import Company
from app.modules.dashboard import drafts as draft_service
from app.modules.dashboard.models import CompanyActivityEvent
from app.modules.processed_emails.models import ProcessedEmail
from app.modules.sheets.service import DRAFT_HEADERS, EMAIL_HEADERS


class DraftWorksheet:
    def __init__(self, rows, *, cell_range):
        self.rows = [list(row) for row in rows]
        self.cell_range = cell_range
        self.requested_ranges = []
        self.updated_cells = []

    def get(self, cell_range):
        self.requested_ranges.append(cell_range)
        if cell_range != self.cell_range:
            raise AssertionError(f"unexpected Sheet range: {cell_range}")
        width = 7 if cell_range == "A:G" else 4
        return [list(row[:width]) for row in self.rows]

    def get_all_values(self):
        return [list(row) for row in self.rows]

    def update_cell(self, row_index, column_index, value):
        self.updated_cells.append((row_index, column_index, value))
        while len(self.rows[row_index - 1]) < column_index:
            self.rows[row_index - 1].append("")
        self.rows[row_index - 1][column_index - 1] = value


class DraftWorkbook:
    def __init__(self, email_rows, draft_rows):
        self.email_sheet = DraftWorksheet(email_rows, cell_range="A:G")
        self.draft_sheet = DraftWorksheet(draft_rows, cell_range="A:D")

    def worksheet(self, name):
        if name == "Emails":
            return self.email_sheet
        if name == "Brouillons":
            return self.draft_sheet
        raise AssertionError(f"unexpected worksheet: {name}")


class DashboardDraftsApiTests(unittest.TestCase):
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
        settings.JWT_SECRET = "dashboard-drafts-api-tests-secret-32-bytes"
        base_time = datetime(2026, 10, 20, 8, tzinfo=UTC)

        with self.session_factory() as db:
            owner = User(email="draft-owner@test.invalid", hashed_password="unused")
            outsider = User(
                email="draft-outsider@test.invalid",
                hashed_password="unused",
            )
            db.add_all([owner, outsider])
            db.flush()
            company = Company(
                user_id=owner.id,
                nom_entreprise="Draft Tenant",
                secteur="Test",
                sheet_id="sheet-drafts-owner",
            )
            foreign_company = Company(
                user_id=outsider.id,
                nom_entreprise="Foreign Tenant",
                secteur="Test",
                sheet_id="sheet-drafts-foreign",
            )
            db.add_all([company, foreign_company])
            db.flush()
            statuses = {
                101: "pending",
                102: "sent",
                103: "discarded",
                104: "missing",
                108: "pending",
                109: "pending",
            }
            for offset, (record_id, draft_status) in enumerate(statuses.items()):
                db.add(
                    self._record(
                        record_id,
                        company.id,
                        draft_status,
                        base_time + timedelta(hours=offset),
                    )
                )
                db.flush()
                db.add(
                    CompanyActivityEvent(
                        company_id=company.id,
                        processed_email_id=record_id,
                        event_type="draft_created",
                        occurred_at=base_time + timedelta(hours=offset, minutes=5),
                    )
                )
            failed_draft = self._record(
                110,
                company.id,
                "pending",
                base_time + timedelta(hours=6),
            )
            failed_draft.status = "failed"
            db.add(failed_draft)
            db.flush()
            db.add(
                CompanyActivityEvent(
                    company_id=company.id,
                    processed_email_id=110,
                    event_type="draft_created",
                    occurred_at=base_time + timedelta(hours=6, minutes=5),
                )
            )
            db.add(
                self._record(
                    201,
                    foreign_company.id,
                    "pending",
                    base_time + timedelta(days=1),
                )
            )
            db.flush()
            db.add(
                CompanyActivityEvent(
                    company_id=foreign_company.id,
                    processed_email_id=201,
                    event_type="draft_created",
                    occurred_at=base_time + timedelta(days=1, minutes=5),
                )
            )
            db.commit()
            self.owner_id = owner.id
            self.outsider_id = outsider.id
            self.company_id = company.id
            self.foreign_company_id = foreign_company.id

        owner_email_rows = [EMAIL_HEADERS]
        owner_draft_rows = [DRAFT_HEADERS]
        details = {
            101: (
                "Alice Example <alice@example.test>",
                "Proposition commerciale",
                "Résumé du devis prioritaire",
                "devis",
                "haute",
                "Contenu confidentiel du devis",
            ),
            102: (
                "Bob <bob@example.test>",
                "Question support",
                "Résumé assistance",
                "support",
                "moyenne",
                "Réponse support déjà envoyée",
            ),
            103: (
                "Charlie <charlie@example.test>",
                "Facture",
                "Résumé facturation",
                "facturation",
                "basse",
                "Réponse volontairement ignorée",
            ),
            104: (
                "Diane <diane@example.test>",
                "Partenariat",
                "Résumé partenaire",
                "partenariat",
                "haute",
                "Brouillon devenu indisponible",
            ),
            108: (
                "Mismatch <mismatch@example.test>",
                "Ligne incohérente",
                "Ne doit pas être exposé",
                "autre",
                "basse",
                "Contenu non fiable",
            ),
            109: (
                "Retry <retry@example.test>",
                "Synchronisation",
                "Résumé synchronisation",
                "support",
                "moyenne",
                "Brouillon à ignorer malgré une panne Sheet",
            ),
            110: (
                "Erreur <erreur@example.test>",
                "Traitement interrompu",
                "Résumé du traitement en erreur",
                "support",
                "haute",
                "Brouillon conservé après une erreur de traitement",
            ),
        }
        for record_id, values in details.items():
            sender, subject, summary, category, priority, content = values
            owner_email_rows.append(
                self._email_row(
                    record_id,
                    sender,
                    subject,
                    summary,
                    category,
                    priority,
                )
            )
            sheet_draft_id = (
                "wrong-draft-108" if record_id == 108 else f"private-draft-{record_id}"
            )
            owner_draft_rows.append(
                [sheet_draft_id, f"EM-{record_id}", content, "En attente"]
            )

        foreign_email_rows = [
            EMAIL_HEADERS,
            self._email_row(
                201,
                "Foreign <foreign@example.test>",
                "Secret étranger",
                "Résumé étranger",
                "autre",
                "basse",
            ),
        ]
        foreign_draft_rows = [
            DRAFT_HEADERS,
            [
                "private-draft-201",
                "EM-201",
                "Contenu du tenant étranger",
                "En attente",
            ],
        ]
        self.workbooks = {
            "sheet-drafts-owner": DraftWorkbook(
                owner_email_rows,
                owner_draft_rows,
            ),
            "sheet-drafts-foreign": DraftWorkbook(
                foreign_email_rows,
                foreign_draft_rows,
            ),
        }
        self.owner_token = create_access_token(
            self.owner_id,
            "draft-owner@test.invalid",
        )
        self.outsider_token = create_access_token(
            self.outsider_id,
            "draft-outsider@test.invalid",
        )
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        settings.JWT_SECRET = self.original_jwt_secret
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def _record(record_id, company_id, draft_status, received_at):
        return ProcessedEmail(
            id=record_id,
            company_id=company_id,
            gmail_message_id=f"private-message-{record_id}",
            gmail_draft_id=f"private-draft-{record_id}",
            callback_token=f"callback-token-{record_id:020d}",
            category="devis",
            priority="haute",
            received_at=received_at,
            draft_status=draft_status,
            status="done",
        )

    @staticmethod
    def _email_row(
        record_id,
        sender,
        subject,
        summary,
        category,
        priority,
    ):
        return [
            f"EM-{record_id}",
            sender,
            subject,
            summary,
            category,
            priority,
            "Brouillon prêt",
            f"private-draft-{record_id}",
        ]

    @staticmethod
    def _auth(token):
        return {"Authorization": f"Bearer {token}"}

    def _workbook(self, sheet_id):
        return self.workbooks[sheet_id]

    def test_list_is_tenant_scoped_searchable_filterable_and_has_metrics(self):
        with patch.object(
            draft_service,
            "get_company_workbook",
            side_effect=self._workbook,
        ):
            response = self.client.get(
                "/api/v1/dashboard/drafts?page=1&page_size=10",
                headers=self._auth(self.owner_token),
            )
            content_search = self.client.get(
                "/api/v1/dashboard/drafts",
                params={"search": "CONFIDENTIEL devis"},
                headers=self._auth(self.owner_token),
            )
            summary_search = self.client.get(
                "/api/v1/dashboard/drafts",
                params={"search": "assistance"},
                headers=self._auth(self.owner_token),
            )
            error_filter = self.client.get(
                "/api/v1/dashboard/drafts",
                params={"status": "en erreur"},
                headers=self._auth(self.owner_token),
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        payload = response.json()
        self.assertEqual(
            payload["metrics"],
            {"pending": 2, "sent": 1, "ignored": 1, "errors": 2},
        )
        self.assertEqual(payload["pagination"]["total_items"], 6)
        self.assertNotIn("BR-108", [item["draft_ref"] for item in payload["items"]])
        for item in payload["items"]:
            self.assertEqual(
                set(item),
                {
                    "draft_ref",
                    "recipient_name",
                    "recipient_email",
                    "subject",
                    "received_at",
                    "prepared_at",
                    "summary",
                    "category",
                    "priority",
                    "status",
                },
            )
        serialized = response.text
        for private_value in (
            "private-draft",
            "private-message",
            "callback-token",
            "company_id",
            "Contenu confidentiel",
            "foreign@example.test",
            "BR-201",
        ):
            self.assertNotIn(private_value, serialized)

        self.assertEqual(
            [item["draft_ref"] for item in content_search.json()["items"]],
            ["BR-101"],
        )
        self.assertEqual(
            [item["draft_ref"] for item in summary_search.json()["items"]],
            ["BR-102"],
        )
        self.assertEqual(
            [item["draft_ref"] for item in error_filter.json()["items"]],
            ["BR-110", "BR-104"],
        )

    def test_detail_returns_full_content_but_no_technical_identifier(self):
        with (
            patch.object(
                draft_service,
                "get_company_workbook",
                side_effect=self._workbook,
            ),
            patch(
                "app.modules.gmail.service.send_company_gmail_draft"
            ) as gmail_send,
        ):
            own = self.client.get(
                "/api/v1/dashboard/drafts/BR-101",
                headers=self._auth(self.owner_token),
            )
            foreign = self.client.get(
                "/api/v1/dashboard/drafts/BR-201",
                headers=self._auth(self.owner_token),
            )
            malformed = self.client.get(
                "/api/v1/dashboard/drafts/private-draft-101",
                headers=self._auth(self.owner_token),
            )

        gmail_send.assert_not_called()
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual(own.json()["content"], "Contenu confidentiel du devis")
        self.assertTrue(own.json()["ai_generated"])
        self.assertEqual(own.json()["recipient_email"], "alice@example.test")
        self.assertNotIn("private-draft-101", own.text)
        self.assertNotIn("callback-token", own.text)
        self.assertEqual(foreign.status_code, 404, foreign.text)
        self.assertEqual(malformed.status_code, 404, malformed.text)

    def test_ignore_is_confirmable_idempotent_and_never_deletes_in_gmail(self):
        with (
            patch.object(
                draft_service,
                "get_company_workbook",
                side_effect=self._workbook,
            ),
            patch(
                "app.modules.gmail.service.send_company_gmail_draft"
            ) as gmail_send,
        ):
            first = self.client.post(
                "/api/v1/dashboard/drafts/BR-101/ignore",
                headers=self._auth(self.owner_token),
            )
            second = self.client.post(
                "/api/v1/dashboard/drafts/BR-101/ignore",
                headers=self._auth(self.owner_token),
            )
            foreign = self.client.post(
                "/api/v1/dashboard/drafts/BR-201/ignore",
                headers=self._auth(self.owner_token),
            )
            sent = self.client.post(
                "/api/v1/dashboard/drafts/BR-102/ignore",
                headers=self._auth(self.owner_token),
            )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(
            first.json(),
            {
                "draft_ref": "BR-101",
                "status": "Ignoré",
                "sheet_sync_pending": False,
            },
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(foreign.status_code, 404, foreign.text)
        self.assertEqual(sent.status_code, 409, sent.text)
        gmail_send.assert_not_called()

        with self.session_factory() as db:
            record = db.get(ProcessedEmail, 101)
            events = db.scalars(
                select(CompanyActivityEvent).where(
                    CompanyActivityEvent.processed_email_id == 101,
                    CompanyActivityEvent.event_type == "draft_discarded",
                )
            ).all()
        self.assertEqual(record.draft_status, "discarded")
        self.assertEqual(record.gmail_draft_id, "private-draft-101")
        self.assertIsNotNone(record.sheet_status_synced_at)
        self.assertEqual(len(events), 1)
        workbook = self.workbooks["sheet-drafts-owner"]
        self.assertIn((2, 7, "Ignoré"), workbook.email_sheet.updated_cells)
        self.assertIn((2, 4, "Ignoré"), workbook.draft_sheet.updated_cells)

    def test_ignore_keeps_safe_success_when_sheet_sync_fails(self):
        with patch.object(
            draft_service,
            "get_company_workbook",
            side_effect=RuntimeError("private-sheet-owner@example.test"),
        ):
            response = self.client.post(
                "/api/v1/dashboard/drafts/BR-109/ignore",
                headers=self._auth(self.owner_token),
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["sheet_sync_pending"])
        self.assertNotIn("private-sheet-owner", response.text)
        with self.session_factory() as db:
            record = db.get(ProcessedEmail, 109)
        self.assertEqual(record.draft_status, "discarded")
        self.assertIsNone(record.sheet_status_synced_at)

    def test_authentication_bounds_and_sheet_errors_are_safe(self):
        unauthenticated = self.client.get("/api/v1/dashboard/drafts")
        self.assertEqual(unauthenticated.status_code, 401, unauthenticated.text)

        for params in (
            {"page": 0},
            {"page_size": 0},
            {"page_size": 101},
            {"search": "x" * 201},
            {"status": "x" * 33},
        ):
            with self.subTest(params=params):
                response = self.client.get(
                    "/api/v1/dashboard/drafts",
                    params=params,
                    headers=self._auth(self.owner_token),
                )
                self.assertEqual(response.status_code, 422, response.text)

        private_error = "private-sheet-owner@example.test"
        with patch.object(
            draft_service,
            "get_company_workbook",
            side_effect=RuntimeError(private_error),
        ):
            listing = self.client.get(
                "/api/v1/dashboard/drafts",
                headers=self._auth(self.owner_token),
            )
            detail = self.client.get(
                "/api/v1/dashboard/drafts/BR-101",
                headers=self._auth(self.owner_token),
            )

        self.assertEqual(listing.status_code, 502, listing.text)
        self.assertEqual(
            listing.json(),
            {"detail": "Les brouillons sont temporairement indisponibles."},
        )
        self.assertEqual(detail.status_code, 502, detail.text)
        self.assertEqual(
            detail.json(),
            {"detail": "Le brouillon est temporairement indisponible."},
        )
        self.assertNotIn(private_error, listing.text + detail.text)


if __name__ == "__main__":
    unittest.main()
