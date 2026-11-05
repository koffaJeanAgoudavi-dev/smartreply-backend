"""Tests d'autorisation et de cloisonnement de l'API administrateur."""

import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.admin_allowlist import parse_admin_user_ids
from app.core.config import settings
from app.core.rate_limit import InMemoryRateLimiter, get_rate_limiter
from app.db.database import Base, get_db
from app.main import app
from app.modules.admin.models import AdminAuditEvent, AdminUserMessage
from app.modules.auth.models import User
from app.modules.auth.security import create_access_token
from app.modules.companies.models import Company
from app.modules.dashboard.models import CompanyActivityEvent  # noqa: F401
from app.modules.drive_oauth.models import DriveConnection
from app.modules.gmail_oauth.models import GmailConnection
from app.modules.processed_emails.models import ProcessedEmail
from app.modules.telegram.models import TelegramConnection, TelegramLinkCode  # noqa: F401


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        self.original_settings = {
            "ADMIN_ALLOWED_USER_IDS": settings.ADMIN_ALLOWED_USER_IDS,
            "ADMIN_ACCESS_CODE": settings.ADMIN_ACCESS_CODE,
            "ADMIN_UNLOCK_RATE_LIMIT": settings.ADMIN_UNLOCK_RATE_LIMIT,
            "ADMIN_UNLOCK_RATE_LIMIT_WINDOW_SECONDS": (
                settings.ADMIN_UNLOCK_RATE_LIMIT_WINDOW_SECONDS
            ),
            "CLIENT_IP_HEADER": settings.CLIENT_IP_HEADER,
            "ENVIRONMENT": settings.ENVIRONMENT,
            "JWT_SECRET": settings.JWT_SECRET,
            "PIPELINE_ALLOWED_COMPANY_IDS": settings.PIPELINE_ALLOWED_COMPANY_IDS,
            "PIPELINE_PROCESSING_STALE_SECONDS": (
                settings.PIPELINE_PROCESSING_STALE_SECONDS
            ),
        }
        settings.JWT_SECRET = "admin-tests-jwt-secret-at-least-32-characters"
        settings.ADMIN_ACCESS_CODE = "Adm7K2qP"
        settings.ADMIN_UNLOCK_RATE_LIMIT = 5
        settings.ADMIN_UNLOCK_RATE_LIMIT_WINDOW_SECONDS = 900
        settings.CLIENT_IP_HEADER = "X-Test-Client-IP"
        settings.ENVIRONMENT = "test"
        settings.PIPELINE_PROCESSING_STALE_SECONDS = 900

        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self._seed_data()

        def override_get_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_get_db
        self.rate_limiter = InMemoryRateLimiter("admin-test-rate-limit-secret")
        app.dependency_overrides[get_rate_limiter] = lambda: self.rate_limiter
        self.client = TestClient(
            app,
            base_url="https://testserver",
            headers={"X-Test-Client-IP": "192.0.2.10"},
        )
        admin_token = create_access_token(self.admin.id, self.admin.email)
        member_token = create_access_token(self.owner_one.id, self.owner_one.email)
        self.admin_headers = {"Authorization": f"Bearer {admin_token}"}
        self.member_headers = {"Authorization": f"Bearer {member_token}"}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()
        for name, value in self.original_settings.items():
            setattr(settings, name, value)

    def _seed_data(self):
        now = datetime.now(timezone.utc)
        self.admin = User(
            email="admin@example.com",
            hashed_password="hashed-admin-password",
        )
        self.owner_one = User(
            email="owner.one@example.com",
            hashed_password="hashed-owner-one",
        )
        self.owner_two = User(
            email="owner.two@example.com",
            hashed_password="hashed-owner-two",
        )
        self.db.add_all((self.admin, self.owner_one, self.owner_two))
        self.db.flush()

        self.company_one = Company(
            user_id=self.owner_one.id,
            nom_entreprise="Alpha Conseil",
            secteur="Conseil",
            sheet_id="secret-sheet-alpha",
            sheet_owner_mode="client",
            response_tone="professional",
            business_description="Contenu métier privé Alpha",
            products_services="Services privés Alpha",
            pipeline_activated_at=now - timedelta(days=5),
            pipeline_last_processed_at=now - timedelta(minutes=5),
        )
        self.company_two = Company(
            user_id=self.owner_two.id,
            nom_entreprise="Beta Studio",
            secteur="Agence",
            sheet_id="secret-sheet-beta",
            sheet_owner_mode="client",
            response_tone="warm",
            pipeline_activated_at=now - timedelta(days=2),
            pipeline_last_processed_at=now - timedelta(hours=2),
        )
        self.db.add_all((self.company_one, self.company_two))
        self.db.flush()

        settings.ADMIN_ALLOWED_USER_IDS = str(self.admin.id)
        settings.PIPELINE_ALLOWED_COMPANY_IDS = str(self.company_one.id)

        self.db.add_all(
            (
                GmailConnection(
                    user_id=self.owner_one.id,
                    encrypted_refresh_token="encrypted-gmail-alpha",
                    status="connected",
                ),
                GmailConnection(
                    user_id=self.owner_two.id,
                    encrypted_refresh_token="encrypted-gmail-beta",
                    status="revoked",
                    updated_at=now - timedelta(hours=1),
                ),
                DriveConnection(
                    user_id=self.owner_one.id,
                    encrypted_refresh_token="encrypted-drive-alpha",
                    status="connected",
                ),
                DriveConnection(
                    user_id=self.owner_two.id,
                    encrypted_refresh_token="encrypted-drive-beta",
                    status="revoked",
                    updated_at=now - timedelta(hours=1),
                ),
                TelegramConnection(
                    user_id=self.owner_one.id,
                    telegram_chat_id=99887766,
                    telegram_user_id=11223344,
                    connected_at=now - timedelta(days=3),
                ),
            )
        )
        self.db.add_all(
            (
                ProcessedEmail(
                    company_id=self.company_one.id,
                    gmail_message_id="private-message-alpha-one",
                    gmail_draft_id="private-draft-alpha-one",
                    status="done",
                    draft_status="pending",
                    attempts=1,
                    category="devis",
                    priority="haute",
                    updated_at=now - timedelta(minutes=10),
                ),
                ProcessedEmail(
                    company_id=self.company_one.id,
                    gmail_message_id="private-message-alpha-two",
                    status="skipped",
                    draft_status="discarded",
                    sheet_status_synced_at=now - timedelta(minutes=8),
                    attempts=1,
                    category="newsletter",
                    priority="basse",
                    updated_at=now - timedelta(minutes=8),
                ),
                ProcessedEmail(
                    company_id=self.company_one.id,
                    gmail_message_id="private-message-alpha-three",
                    status="failed",
                    attempts=3,
                    last_error_code="gmail_quota_429",
                    updated_at=now - timedelta(minutes=6),
                ),
                ProcessedEmail(
                    company_id=self.company_two.id,
                    gmail_message_id="private-message-beta-one",
                    status="processing",
                    attempts=2,
                    updated_at=now - timedelta(hours=1),
                ),
                ProcessedEmail(
                    company_id=self.company_two.id,
                    gmail_message_id="private-message-beta-two",
                    status="done",
                    draft_status="sent",
                    attempts=1,
                    notification_attempts=3,
                    notification_blocked_at=now - timedelta(minutes=30),
                    sheet_status_synced_at=None,
                    updated_at=now - timedelta(minutes=30),
                ),
            )
        )
        self.db.commit()

    def _unlock_admin(self, code: str = "Adm7K2qP"):
        response = self.client.post(
            "/api/v1/admin/unlock",
            headers=self.admin_headers,
            json={"code": code},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_admin_allowlist_parser_is_strict_and_deduplicates_ids(self):
        self.assertEqual(parse_admin_user_ids(""), frozenset())
        self.assertEqual(
            parse_admin_user_ids(" 2, 1, 2 "),
            frozenset({1, 2}),
        )
        for invalid in ("0", "-1", "1.5", "1,", "1,member"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    ValueError,
                    "ADMIN_ALLOWED_USER_IDS invalide",
                ):
                    parse_admin_user_ids(invalid)

    def test_admin_access_is_fail_closed(self):
        missing = self.client.get("/api/v1/admin/me")
        member = self.client.get("/api/v1/admin/me", headers=self.member_headers)
        member_unlock = self.client.post(
            "/api/v1/admin/unlock",
            headers=self.member_headers,
            json={"code": settings.ADMIN_ACCESS_CODE},
        )
        locked = self.client.get("/api/v1/admin/me", headers=self.admin_headers)
        access = self.client.get("/api/v1/admin/access", headers=self.admin_headers)

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(member.status_code, 403)
        self.assertEqual(member_unlock.status_code, 403)
        self.assertNotIn("set-cookie", member_unlock.headers)
        self.assertEqual(member.json(), {"detail": "Accès administrateur refusé"})
        self.assertEqual(locked.status_code, 423)
        self.assertEqual(locked.json(), {"detail": "Code administrateur requis"})
        self.assertEqual(access.status_code, 200)
        self.assertFalse(access.json()["unlocked"])
        self.assertIsNone(access.json()["expires_at"])

        self._unlock_admin()
        allowed = self.client.get("/api/v1/admin/me", headers=self.admin_headers)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(
            allowed.json(),
            {"user_id": self.admin.id, "email": self.admin.email},
        )

        settings.ADMIN_ALLOWED_USER_IDS = ""
        empty_allowlist = self.client.get(
            "/api/v1/admin/me",
            headers=self.admin_headers,
        )
        self.assertEqual(empty_allowlist.status_code, 403)

        settings.ADMIN_ALLOWED_USER_IDS = "invalid"
        invalid_configuration = self.client.get(
            "/api/v1/admin/me",
            headers=self.admin_headers,
        )
        self.assertEqual(invalid_configuration.status_code, 503)
        self.assertEqual(
            invalid_configuration.json(),
            {"detail": "Configuration administrateur indisponible"},
        )

    def test_admin_code_unlock_uses_a_short_secure_cookie_session(self):
        wrong = self.client.post(
            "/api/v1/admin/unlock",
            headers=self.admin_headers,
            json={"code": "Bad8Code"},
        )
        self.assertEqual(wrong.status_code, 403)
        self.assertEqual(wrong.json(), {"detail": "Code administrateur invalide"})
        self.assertNotIn("set-cookie", wrong.headers)

        settings.ENVIRONMENT = "production"
        unlocked = self._unlock_admin()
        cookie = unlocked.headers["set-cookie"]
        self.assertIn("smartreply_admin_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Max-Age=1800", cookie)
        self.assertIn("Path=/api/v1/admin", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertIn("Secure", cookie)
        self.assertNotIn(settings.ADMIN_ACCESS_CODE, unlocked.text)
        self.assertTrue(unlocked.json()["unlocked"])

        access = self.client.get("/api/v1/admin/access", headers=self.admin_headers)
        self.assertTrue(access.json()["unlocked"])
        self.assertIsNotNone(access.json()["expires_at"])

        settings.ADMIN_ALLOWED_USER_IDS = f"{self.admin.id},{self.owner_one.id}"
        other_account = self.client.get(
            "/api/v1/admin/me",
            headers=self.member_headers,
        )
        self.assertEqual(other_account.status_code, 423)
        settings.ADMIN_ALLOWED_USER_IDS = str(self.admin.id)

        settings.ADMIN_ACCESS_CODE = "New8Code"
        rotated = self.client.get("/api/v1/admin/me", headers=self.admin_headers)
        self.assertEqual(rotated.status_code, 423)

        settings.ADMIN_ACCESS_CODE = "Adm7K2qP"
        self._unlock_admin()
        locked = self.client.post("/api/v1/admin/lock", headers=self.admin_headers)
        self.assertEqual(locked.status_code, 204)
        after_lock = self.client.get("/api/v1/admin/me", headers=self.admin_headers)
        self.assertEqual(after_lock.status_code, 423)

    def test_admin_code_configuration_and_attempts_fail_closed(self):
        settings.ADMIN_ACCESS_CODE = ""
        unavailable = self.client.get(
            "/api/v1/admin/access",
            headers=self.admin_headers,
        )
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(
            unavailable.json(),
            {"detail": "Configuration du code administrateur indisponible"},
        )

        settings.ADMIN_ACCESS_CODE = "NineChars"
        wrong_length = self.client.get(
            "/api/v1/admin/access",
            headers=self.admin_headers,
        )
        self.assertEqual(wrong_length.status_code, 503)

        settings.ADMIN_ACCESS_CODE = "Adm7K2qP"
        settings.ADMIN_UNLOCK_RATE_LIMIT = 2
        for _ in range(2):
            rejected = self.client.post(
                "/api/v1/admin/unlock",
                headers=self.admin_headers,
                json={"code": "Nope7Key"},
            )
            self.assertEqual(rejected.status_code, 403)
        limited = self.client.post(
            "/api/v1/admin/unlock",
            headers=self.admin_headers,
            json={"code": settings.ADMIN_ACCESS_CODE},
        )
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)
        self.assertNotIn(settings.ADMIN_ACCESS_CODE, limited.text)

    def test_overview_uses_neon_metadata_without_external_content(self):
        self._unlock_admin()
        response = self.client.get(
            "/api/v1/admin/overview",
            headers=self.admin_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["metrics"],
            {
                "users": 3,
                "companies": 2,
                "validated_companies": 1,
                "pending_companies": 1,
                "ready_companies": 1,
                "incomplete_companies": 1,
                "gmail_issues": 1,
                "drive_issues": 1,
                "telegram_connected": 1,
                "processed_emails": 3,
                "failed_emails": 1,
                "pending_drafts": 1,
                "active_incidents": 6,
            },
        )
        self.assertIsNotNone(payload["last_processing_at"])
        serialized = response.text
        for forbidden_value in (
            "secret-sheet-alpha",
            "encrypted-gmail-alpha",
            "private-message-alpha-one",
            "99887766",
            "Contenu métier privé Alpha",
        ):
            self.assertNotIn(forbidden_value, serialized)

    def test_company_list_is_paginated_filterable_and_secret_free(self):
        self._unlock_admin()
        all_companies = self.client.get(
            "/api/v1/admin/companies?page=1&page_size=1",
            headers=self.admin_headers,
        )
        validated = self.client.get(
            "/api/v1/admin/companies?validation_status=validated",
            headers=self.admin_headers,
        )
        telegram_missing = self.client.get(
            "/api/v1/admin/companies?telegram_connected=false",
            headers=self.admin_headers,
        )
        search = self.client.get(
            "/api/v1/admin/companies?search=alpha",
            headers=self.admin_headers,
        )

        self.assertEqual(all_companies.status_code, 200)
        self.assertEqual(all_companies.json()["pagination"]["total_items"], 2)
        self.assertEqual(all_companies.json()["pagination"]["total_pages"], 2)
        self.assertEqual(len(all_companies.json()["items"]), 1)
        self.assertEqual(
            [item["name"] for item in validated.json()["items"]],
            ["Alpha Conseil"],
        )
        self.assertEqual(
            [item["name"] for item in telegram_missing.json()["items"]],
            ["Beta Studio"],
        )
        self.assertEqual(
            [item["name"] for item in search.json()["items"]],
            ["Alpha Conseil"],
        )
        for response in (all_companies, validated, telegram_missing, search):
            for forbidden_value in (
                "secret-sheet",
                "encrypted-",
                "private-message",
                "telegram_chat_id",
                "business_description",
            ):
                self.assertNotIn(forbidden_value, response.text)

    def test_company_detail_exposes_completeness_not_profile_content(self):
        self._unlock_admin()
        response = self.client.get(
            f"/api/v1/admin/companies/{self.company_one.id}",
            headers=self.admin_headers,
        )
        missing = self.client.get(
            "/api/v1/admin/companies/9999",
            headers=self.admin_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["company_id"], self.company_one.id)
        self.assertEqual(payload["validation_status"], "validated")
        self.assertEqual(payload["configuration_status"], "ready")
        self.assertEqual(payload["profile"]["configured_fields"], 2)
        self.assertEqual(payload["profile"]["total_fields"], 6)
        self.assertEqual(payload["profile"]["response_tone"], "professional")
        self.assertNotIn("Contenu métier privé Alpha", response.text)
        self.assertNotIn("Services privés Alpha", response.text)
        self.assertNotIn("secret-sheet-alpha", response.text)
        self.assertNotIn("encrypted-gmail-alpha", response.text)
        self.assertEqual(missing.status_code, 404)

    def test_admin_v2_message_suspension_and_tenant_isolation(self):
        self._unlock_admin()
        message_body = "Une maintenance est prévue demain à 18 h."
        sent = self.client.post(
            f"/api/v1/admin/companies/{self.company_one.id}/messages",
            headers=self.admin_headers,
            json={"title": "Maintenance planifiée", "body": message_body},
        )
        self.assertEqual(sent.status_code, 201, sent.text)
        self.assertNotIn(message_body, sent.text)
        notice_ref = sent.json()["message_ref"]

        owner_two_token = create_access_token(
            self.owner_two.id,
            self.owner_two.email,
        )
        owner_two_headers = {"Authorization": f"Bearer {owner_two_token}"}
        owner_notices = self.client.get(
            "/api/v1/dashboard/notices",
            headers=self.member_headers,
        )
        other_notices = self.client.get(
            "/api/v1/dashboard/notices",
            headers=owner_two_headers,
        )
        cross_tenant_read = self.client.post(
            f"/api/v1/dashboard/notices/{notice_ref}/read",
            headers=owner_two_headers,
        )
        self.assertEqual(owner_notices.status_code, 200)
        self.assertEqual(owner_notices.json()["unread_count"], 1)
        self.assertEqual(
            owner_notices.json()["latest_unread"]["notice_ref"],
            notice_ref,
        )
        self.assertEqual(owner_notices.json()["items"][0]["body"], message_body)
        self.assertEqual(other_notices.json()["items"], [])
        self.assertEqual(cross_tenant_read.status_code, 404)

        read = self.client.post(
            f"/api/v1/dashboard/notices/{notice_ref}/read",
            headers=self.member_headers,
        )
        self.assertEqual(read.status_code, 200)
        self.assertIsNotNone(read.json()["read_at"])
        after_read = self.client.get(
            "/api/v1/dashboard/notices",
            headers=self.member_headers,
        )
        self.assertEqual(after_read.json()["unread_count"], 0)
        self.assertIsNone(after_read.json()["latest_unread"])

        suspended_until = datetime.now(timezone.utc) + timedelta(hours=4)
        suspended = self.client.put(
            f"/api/v1/admin/companies/{self.company_one.id}/suspension",
            headers=self.admin_headers,
            json={
                "reason": "Maintenance temporaire du traitement",
                "suspended_until": suspended_until.isoformat(),
            },
        )
        self.assertEqual(suspended.status_code, 200, suspended.text)
        self.assertEqual(suspended.json()["operational_status"], "suspended")
        self.assertEqual(
            settings.PIPELINE_ALLOWED_COMPANY_IDS,
            str(self.company_one.id),
        )

        owner_status = self.client.get(
            "/api/v1/dashboard/operational-status",
            headers=self.member_headers,
        )
        dashboard_still_accessible = self.client.get(
            "/api/v1/dashboard/overview",
            headers=self.member_headers,
        )
        self.assertEqual(owner_status.status_code, 200)
        self.assertEqual(owner_status.json()["operational_status"], "suspended")
        self.assertEqual(
            owner_status.json()["suspension_reason"],
            "Maintenance temporaire du traitement",
        )
        self.assertEqual(dashboard_still_accessible.status_code, 200)

        detail = self.client.get(
            f"/api/v1/admin/companies/{self.company_one.id}",
            headers=self.admin_headers,
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["operational_status"], "suspended")
        self.assertEqual(
            [item["event_type"] for item in detail.json()["audit_events"]],
            ["company_suspended", "message_sent"],
        )
        self.assertNotIn(message_body, detail.text)

        self.db.expire_all()
        stored_company = self.db.get(Company, self.company_one.id)
        stored_company.suspended_until = datetime.now(timezone.utc) - timedelta(
            minutes=1
        )
        self.db.commit()
        automatically_active = self.client.get(
            "/api/v1/dashboard/operational-status",
            headers=self.member_headers,
        )
        self.assertEqual(
            automatically_active.json()["operational_status"],
            "active",
        )
        self.assertIsNone(automatically_active.json()["suspension_reason"])

        resumed = self.client.delete(
            f"/api/v1/admin/companies/{self.company_one.id}/suspension",
            headers=self.admin_headers,
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()["operational_status"], "active")
        after_resume = self.client.get(
            "/api/v1/dashboard/operational-status",
            headers=self.member_headers,
        )
        self.assertEqual(after_resume.json()["operational_status"], "active")
        self.assertIsNone(after_resume.json()["suspension_reason"])

        self.db.expire_all()
        self.assertEqual(self.db.query(AdminUserMessage).count(), 1)
        self.assertEqual(self.db.query(AdminAuditEvent).count(), 3)

    def test_admin_v2_actions_validate_input_and_remain_admin_only(self):
        self._unlock_admin()
        member_message = self.client.post(
            f"/api/v1/admin/companies/{self.company_one.id}/messages",
            headers=self.member_headers,
            json={"title": "Titre", "body": "Message"},
        )
        empty_message = self.client.post(
            f"/api/v1/admin/companies/{self.company_one.id}/messages",
            headers=self.admin_headers,
            json={"title": "   ", "body": "Message"},
        )
        empty_reason = self.client.put(
            f"/api/v1/admin/companies/{self.company_one.id}/suspension",
            headers=self.admin_headers,
            json={"reason": "   ", "suspended_until": None},
        )
        past_suspension = self.client.put(
            f"/api/v1/admin/companies/{self.company_one.id}/suspension",
            headers=self.admin_headers,
            json={
                "reason": "Date invalide",
                "suspended_until": (
                    datetime.now(timezone.utc) - timedelta(minutes=1)
                ).isoformat(),
            },
        )
        self.assertEqual(member_message.status_code, 403)
        self.assertEqual(empty_message.status_code, 422)
        self.assertEqual(empty_reason.status_code, 422)
        self.assertEqual(past_suspension.status_code, 409)
        self.assertEqual(self.db.query(AdminUserMessage).count(), 0)
        self.assertEqual(self.db.query(AdminAuditEvent).count(), 0)

    def test_incidents_use_only_safe_internal_references(self):
        self._unlock_admin()
        response = self.client.get(
            "/api/v1/admin/incidents?page=1&page_size=25",
            headers=self.admin_headers,
        )
        critical = self.client.get(
            "/api/v1/admin/incidents?severity=critical",
            headers=self.admin_headers,
        )
        beta_only = self.client.get(
            f"/api/v1/admin/incidents?company_id={self.company_two.id}",
            headers=self.admin_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["pagination"]["total_items"], 6)
        incident_types = {item["incident_type"] for item in payload["items"]}
        self.assertEqual(
            incident_types,
            {
                "gmail_revoked",
                "drive_revoked",
                "processing_failed",
                "processing_stale",
                "notification_blocked",
                "sheet_sync_pending",
            },
        )
        self.assertIn("gmail_quota_429", response.text)
        self.assertTrue(
            all(item["severity"] == "critical" for item in critical.json()["items"])
        )
        self.assertTrue(
            all(
                item["company_id"] == self.company_two.id
                for item in beta_only.json()["items"]
            )
        )
        for forbidden_value in (
            "private-message",
            "private-draft",
            "secret-sheet",
            "encrypted-",
            "99887766",
        ):
            self.assertNotIn(forbidden_value, response.text)


if __name__ == "__main__":
    unittest.main()
