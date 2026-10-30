"""Tests du Dashboard Phase B et de son isolation par entreprise."""

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
from app.modules.dashboard import service as dashboard_service
from app.modules.dashboard.models import CompanyActivityEvent
from app.modules.processed_emails.models import ProcessedEmail
from app.modules.processed_emails.service import (
    claim_draft_discard,
    claim_draft_send,
    mark_done,
    mark_notification_sent,
    record_gmail_draft_id,
    reserve_email,
    transition_draft_status,
)
from app.modules.sheets.service import EMAIL_HEADERS


class ReadOnlyWorksheet:
    def __init__(self, rows):
        self.rows = [list(row) for row in rows]

    def get(self, cell_range):
        if cell_range == "A:C":
            return [list(row[:3]) for row in self.rows]
        if cell_range == "E:G":
            return [list(row[4:7]) for row in self.rows]
        raise AssertionError("dashboard requested a private Sheet column")

    def get_all_values(self):
        raise AssertionError("dashboard must not read summaries or draft IDs")

    def append_row(self, *_args, **_kwargs):
        raise AssertionError("dashboard reads must never append Sheet rows")

    def update(self, *_args, **_kwargs):
        raise AssertionError("dashboard reads must never update Sheet rows")

    def update_cell(self, *_args, **_kwargs):
        raise AssertionError("dashboard reads must never update Sheet cells")


class ReadOnlyWorkbook:
    def __init__(self, rows):
        self.email_sheet = ReadOnlyWorksheet(rows)

    def worksheet(self, name):
        if name != "Emails":
            raise AssertionError("dashboard must only read the Emails worksheet")
        return self.email_sheet


class DashboardPhaseBApiTests(unittest.TestCase):
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
        settings.JWT_SECRET = "dashboard-phase-b-tests-secret-32-bytes"
        now = datetime.now(UTC)

        with self.session_factory() as db:
            user_a = User(email="owner-a@test.invalid", hashed_password="unused")
            user_b = User(email="owner-b@test.invalid", hashed_password="unused")
            db.add_all([user_a, user_b])
            db.flush()
            company_a = Company(
                user_id=user_a.id,
                nom_entreprise="Tenant A",
                secteur="Test",
                sheet_id="sheet-a-private",
            )
            company_b = Company(
                user_id=user_b.id,
                nom_entreprise="Tenant B",
                secteur="Test",
                sheet_id="sheet-b-private",
            )
            db.add_all([company_a, company_b])
            db.flush()
            record_a_done = ProcessedEmail(
                company_id=company_a.id,
                gmail_message_id="message-a-done",
                gmail_draft_id="draft-a",
                callback_token="A" * 32,
                draft_status="pending",
                status="done",
                attempts=1,
                category="support",
                priority="haute",
                received_at=now - timedelta(hours=1),
            )
            record_a_skipped = ProcessedEmail(
                company_id=company_a.id,
                gmail_message_id="message-a-skipped",
                status="skipped",
                attempts=1,
                category="spam",
                priority="basse",
                received_at=now - timedelta(hours=2),
            )
            record_b = ProcessedEmail(
                company_id=company_b.id,
                gmail_message_id="message-b",
                gmail_draft_id="draft-b",
                callback_token="B" * 32,
                draft_status="pending",
                status="done",
                attempts=1,
                category="facturation",
                priority="moyenne",
                received_at=now - timedelta(minutes=30),
            )
            db.add_all([record_a_done, record_a_skipped, record_b])
            db.flush()
            db.add_all(
                [
                    CompanyActivityEvent(
                        company_id=company_a.id,
                        processed_email_id=record_a_done.id,
                        event_type="email_processed",
                        occurred_at=now - timedelta(minutes=20),
                    ),
                    CompanyActivityEvent(
                        company_id=company_a.id,
                        processed_email_id=record_a_skipped.id,
                        event_type="email_skipped",
                        occurred_at=now - timedelta(minutes=10),
                    ),
                    CompanyActivityEvent(
                        company_id=company_b.id,
                        processed_email_id=record_b.id,
                        event_type="email_processed",
                        occurred_at=now - timedelta(minutes=5),
                    ),
                ]
            )
            db.commit()
            self.user_a_id = user_a.id
            self.user_b_id = user_b.id
            self.company_a_id = company_a.id
            self.company_b_id = company_b.id
            self.record_a_id = record_a_done.id
            self.record_b_id = record_b.id

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
    def auth(token):
        return {"Authorization": f"Bearer {token}"}

    def test_overview_uses_only_the_authenticated_company(self):
        response = self.client.get(
            f"/api/v1/dashboard/overview?company_id={self.company_b_id}",
            headers=self.auth(self.token_a),
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(
            payload["metrics"],
            {
                "analyzed_emails": 2,
                "processed_emails": 1,
                "ignored_emails": 1,
                "pending_drafts": 1,
            },
        )
        self.assertEqual(sum(point["count"] for point in payload["volume"]), 2)
        self.assertEqual(
            {item["key"]: item["count"] for item in payload["categories"]},
            {"support": 1, "spam": 1},
        )
        self.assertEqual(
            {item["key"]: item["count"] for item in payload["priorities"]},
            {"haute": 1, "basse": 1},
        )
        self.assertEqual(len(payload["recent_activity"]), 2)
        serialized = response.text
        self.assertNotIn("facturation", serialized)
        self.assertNotIn("message-", serialized)
        self.assertNotIn("sheet-", serialized)
        self.assertNotIn("draft-", serialized)

    def test_overview_requires_a_valid_jwt(self):
        response = self.client.get("/api/v1/dashboard/overview")
        self.assertEqual(response.status_code, 401, response.text)

    def test_overview_requires_the_authenticated_users_company(self):
        with self.session_factory() as db:
            user = User(email="without-company@test.invalid", hashed_password="unused")
            db.add(user)
            db.commit()
            user_id = user.id
        token = create_access_token(user_id, "without-company@test.invalid")

        response = self.client.get(
            "/api/v1/dashboard/overview",
            headers=self.auth(token),
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(
            response.json(),
            {"detail": "Aucune entreprise trouvée pour cet utilisateur."},
        )

    def test_sender_cleanup_never_returns_an_address(self):
        self.assertEqual(
            dashboard_service._display_sender(
                '"private-a@test.invalid" <hidden@test.invalid>'
            ),
            "Expéditeur",
        )
        self.assertEqual(
            dashboard_service._display_sender("Display name <hidden@test.invalid>"),
            "Display name",
        )
        self.assertEqual(
            dashboard_service._display_sender("Display name"),
            "Display name",
        )

    def test_recent_emails_open_only_the_owner_sheet_and_filter_foreign_ids(self):
        workbook_a = ReadOnlyWorkbook(
            [
                EMAIL_HEADERS,
                [
                    f"EM-{self.record_a_id}",
                    "Display A <private-a@test.invalid>",
                    "Subject A",
                    "Private summary A",
                    "support",
                    "haute",
                    "Brouillon prêt",
                    "draft-a",
                ],
                [
                    f"EM-{self.record_b_id}",
                    "Display B <private-b@test.invalid>",
                    "Subject B",
                    "Private summary B",
                    "facturation",
                    "moyenne",
                    "Brouillon prêt",
                    "draft-b",
                ],
            ]
        )

        def workbook(sheet_id):
            self.assertEqual(sheet_id, "sheet-a-private")
            return workbook_a

        with patch.object(
            dashboard_service,
            "get_company_workbook",
            side_effect=workbook,
        ) as get_workbook:
            response = self.client.get(
                "/api/v1/dashboard/recent-emails",
                headers=self.auth(self.token_a),
            )

        self.assertEqual(response.status_code, 200, response.text)
        get_workbook.assert_called_once_with("sheet-a-private")
        emails = response.json()["emails"]
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]["sender"], "Display A")
        self.assertEqual(emails[0]["subject"], "Subject A")
        self.assertIsNotNone(emails[0]["received_at"])
        self.assertNotIn("@", response.text)
        self.assertNotIn("Private summary", response.text)
        self.assertNotIn("draft-a", response.text)
        self.assertNotIn("Subject B", response.text)

    def test_second_tenant_reads_only_its_own_sheet(self):
        workbook_b = ReadOnlyWorkbook(
            [
                EMAIL_HEADERS,
                [
                    f"EM-{self.record_b_id}",
                    "Display B <private-b@test.invalid>",
                    "Subject B",
                    "Private summary B",
                    "facturation",
                    "moyenne",
                    "Brouillon prêt",
                    "draft-b",
                ],
            ]
        )

        def workbook(sheet_id):
            self.assertEqual(sheet_id, "sheet-b-private")
            return workbook_b

        with patch.object(
            dashboard_service,
            "get_company_workbook",
            side_effect=workbook,
        ):
            response = self.client.get(
                "/api/v1/dashboard/recent-emails",
                headers=self.auth(self.token_b),
            )

        self.assertEqual(response.status_code, 200, response.text)
        emails = response.json()["emails"]
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]["sender"], "Display B")
        self.assertEqual(emails[0]["subject"], "Subject B")
        self.assertNotIn("Subject A", response.text)
        self.assertNotIn("@", response.text)

    def test_recent_emails_support_empty_and_sheet_error_states(self):
        with patch.object(
            dashboard_service,
            "get_company_workbook",
            return_value=ReadOnlyWorkbook([EMAIL_HEADERS]),
        ):
            empty = self.client.get(
                "/api/v1/dashboard/recent-emails",
                headers=self.auth(self.token_a),
            )
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json(), {"emails": []})

        with patch.object(
            dashboard_service,
            "get_company_workbook",
            side_effect=RuntimeError("private-sheet-diagnostic"),
        ):
            failed = self.client.get(
                "/api/v1/dashboard/recent-emails",
                headers=self.auth(self.token_a),
            )
        self.assertEqual(failed.status_code, 502, failed.text)
        self.assertEqual(
            failed.json(),
            {"detail": "Les emails récents sont temporairement indisponibles."},
        )
        self.assertNotIn("private-sheet-diagnostic", failed.text)


class DashboardActivityTransitionTests(unittest.TestCase):
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
        with self.session_factory() as db:
            user = User(email="events@test.invalid", hashed_password="unused")
            db.add(user)
            db.flush()
            company = Company(
                user_id=user.id,
                nom_entreprise="Events",
                secteur="Test",
                sheet_id="events-sheet",
            )
            db.add(company)
            db.commit()
            self.company_id = company.id

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def create_done_draft(self, label):
        with self.session_factory() as db:
            reservation = reserve_email(
                db,
                company_id=self.company_id,
                gmail_message_id=f"message-{label}",
            )
        with self.session_factory() as db:
            reservation = record_gmail_draft_id(
                db,
                reservation,
                gmail_draft_id=f"draft-{label}",
            )
        with self.session_factory() as db:
            mark_done(
                db,
                reservation,
                category="support",
                priority="moyenne",
                received_at=datetime.now(UTC),
            )
        return reservation

    def test_activity_table_contains_no_email_or_draft_content(self):
        self.assertEqual(
            set(CompanyActivityEvent.__table__.columns.keys()),
            {
                "id",
                "company_id",
                "processed_email_id",
                "event_type",
                "occurred_at",
            },
        )

    def test_successful_transitions_create_each_activity_only_once(self):
        reservation = self.create_done_draft("send")
        with self.session_factory() as db:
            mark_notification_sent(
                db,
                company_id=self.company_id,
                record_id=reservation.record_id,
            )
        with self.session_factory() as db:
            self.assertTrue(
                claim_draft_send(
                    db,
                    company_id=self.company_id,
                    record_id=reservation.record_id,
                )
            )
        with self.session_factory() as db:
            self.assertTrue(
                transition_draft_status(
                    db,
                    company_id=self.company_id,
                    record_id=reservation.record_id,
                    expected_status="sending",
                    target_status="sent",
                )
            )
        with self.session_factory() as db:
            self.assertFalse(
                transition_draft_status(
                    db,
                    company_id=self.company_id,
                    record_id=reservation.record_id,
                    expected_status="sending",
                    target_status="sent",
                )
            )
            event_types = db.scalars(
                select(CompanyActivityEvent.event_type).where(
                    CompanyActivityEvent.processed_email_id == reservation.record_id
                )
            ).all()
        self.assertEqual(
            set(event_types),
            {
                "draft_created",
                "email_processed",
                "notification_sent",
                "draft_sent",
            },
        )
        self.assertEqual(len(event_types), len(set(event_types)))

    def test_failed_discard_replay_creates_no_duplicate_activity(self):
        reservation = self.create_done_draft("discard")
        with self.session_factory() as db:
            first = claim_draft_discard(
                db,
                company_id=self.company_id,
                record_id=reservation.record_id,
            )
        with self.session_factory() as db:
            second = claim_draft_discard(
                db,
                company_id=self.company_id,
                record_id=reservation.record_id,
            )
            discard_events = db.scalars(
                select(CompanyActivityEvent).where(
                    CompanyActivityEvent.processed_email_id == reservation.record_id,
                    CompanyActivityEvent.event_type == "draft_discarded",
                )
            ).all()
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(discard_events), 1)


if __name__ == "__main__":
    unittest.main()
