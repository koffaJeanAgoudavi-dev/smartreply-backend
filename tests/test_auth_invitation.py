"""Tests de la protection de l'inscription par codes d'invitation."""

import hmac
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.database import Base, get_db
from app.main import app
from app.modules.auth.models import User


class InvitationRegistrationTests(unittest.TestCase):
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
        self.original_invitation_codes = settings.INVITATION_CODES
        settings.JWT_SECRET = "invitation-tests-secret-at-least-32-bytes"
        settings.INVITATION_CODES = "invite-alpha"
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        settings.JWT_SECRET = self.original_jwt_secret
        settings.INVITATION_CODES = self.original_invitation_codes
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def register(self, email: str, invitation_code=...):
        payload = {
            "email": email,
            "password": "motdepasse123",
        }
        if invitation_code is not ...:
            payload["invitation_code"] = invitation_code
        return self.client.post("/api/v1/auth/register", json=payload)

    def user_count(self) -> int:
        with self.session_factory() as db:
            return db.scalar(select(func.count()).select_from(User))

    def assert_generic_invitation_refusal(self, response):
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json(), {"detail": "Code d'invitation invalide"})
        self.assertEqual(self.user_count(), 0)

    def test_valid_code_registers_without_exposing_it_and_login_is_unchanged(self):
        response = self.register("valid@example.com", "invite-alpha")

        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn("invitation", response.json())
        self.assertNotIn("invite-alpha", response.text)
        self.assertEqual(self.user_count(), 1)

        login_response = self.client.post(
            "/api/v1/auth/login",
            json={
                "email": "valid@example.com",
                "password": "motdepasse123",
            },
        )
        self.assertEqual(login_response.status_code, 200, login_response.text)
        self.assertIn("access_token", login_response.json())

    def test_invalid_code_is_refused_with_a_generic_403(self):
        response = self.register("invalid@example.com", "wrong-code")
        self.assert_generic_invitation_refusal(response)

    def test_absent_code_is_refused_with_the_same_generic_403(self):
        response = self.register("absent@example.com")
        self.assert_generic_invitation_refusal(response)

    def test_registration_is_closed_when_configuration_is_empty(self):
        settings.INVITATION_CODES = ""
        response = self.register("closed@example.com", "invite-alpha")
        self.assert_generic_invitation_refusal(response)

    def test_multiple_codes_are_supported_and_one_can_be_revoked(self):
        settings.INVITATION_CODES = "invite-alpha, invite-beta,invite-gamma"
        accepted = self.register("second-code@example.com", "invite-beta")
        self.assertEqual(accepted.status_code, 201, accepted.text)

        settings.INVITATION_CODES = "invite-alpha,invite-gamma"
        revoked = self.register("revoked@example.com", "invite-beta")
        self.assertEqual(revoked.status_code, 403, revoked.text)
        self.assertEqual(revoked.json(), {"detail": "Code d'invitation invalide"})
        self.assertEqual(self.user_count(), 1)

    def test_every_configured_code_is_compared_without_early_exit(self):
        settings.INVITATION_CODES = "invite-alpha,invite-beta,invite-gamma"
        original_compare_digest = hmac.compare_digest

        with patch(
            "app.modules.auth.router.hmac.compare_digest",
            wraps=original_compare_digest,
        ) as compare_digest:
            response = self.register("all-compared@example.com", "invite-alpha")

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(compare_digest.call_count, 3)


if __name__ == "__main__":
    unittest.main()
