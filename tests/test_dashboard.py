"""Tests du montage statique du dashboard et du retour OAuth Gmail."""

import re
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.db.database import get_db
from app.main import app
from app.modules.gmail_oauth import router as gmail_router


class DashboardStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_dashboard_path_redirects_to_its_static_index(self):
        response = self.client.get("/dashboard", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "http://testserver/dashboard/")

    def test_dashboard_index_is_served_in_french_without_external_assets(self):
        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn('lang="fr"', response.text)
        self.assertIn("Tableau de bord", response.text)
        self.assertIn("Intégrations", response.text)
        self.assertIn("Bientôt", response.text)
        self.assertIn("Vos statistiques apparaîtront", response.text)
        for excluded_feature in (
            "Plan Pro",
            "Système actif",
            "Activité récente",
            "Derniers emails traités",
        ):
            self.assertNotIn(excluded_feature, response.text)
        self.assertNotRegex(
            response.text,
            re.compile(r'<(?:script|link)[^>]+(?:src|href)=["\']https?://', re.I),
        )

    def test_dashboard_css_and_javascript_are_served(self):
        css_response = self.client.get("/dashboard/styles.css")
        js_response = self.client.get("/dashboard/app.js")

        self.assertEqual(css_response.status_code, 200)
        self.assertIn("text/css", css_response.headers["content-type"])
        self.assertIn("--color-primary-700", css_response.text)
        self.assertIn("--radius-lg", css_response.text)
        self.assertIn("--shadow-card", css_response.text)
        self.assertEqual(js_response.status_code, 200)
        self.assertIn("javascript", js_response.headers["content-type"])
        self.assertIn('"smartreply_access_token"', js_response.text)
        self.assertIn('"/api/v1/gmail/connect"', js_response.text)
        self.assertIn('"/api/v1/telegram/link-code"', js_response.text)

    def test_existing_health_endpoint_is_unchanged(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class GmailCallbackDashboardRedirectTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()

        def override_get_db():
            yield self.db

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()

    def test_connect_still_returns_an_authorization_url_as_json(self):
        app.dependency_overrides[gmail_router.get_current_user_id] = lambda: 42
        with patch.object(
            gmail_router.service,
            "build_authorization_url",
            return_value="https://accounts.google.com/o/oauth2/v2/auth?state=signed",
        ) as build_url:
            response = self.client.get("/api/v1/gmail/connect")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "authorization_url": (
                    "https://accounts.google.com/o/oauth2/v2/auth?state=signed"
                )
            },
        )
        build_url.assert_called_once()

    def test_successful_callback_saves_token_then_redirects_to_integrations(self):
        with (
            patch.object(gmail_router, "verify_state", return_value=42),
            patch.object(
                gmail_router.service,
                "exchange_code_for_refresh_token",
                new=AsyncMock(return_value="test-refresh-token"),
            ) as exchange,
            patch.object(gmail_router.service, "save_refresh_token") as save,
        ):
            response = self.client.get(
                "/api/v1/gmail/callback?code=google-code&state=signed-state",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            "/dashboard/?gmail=connected#integrations",
        )
        self.assertNotIn("test-refresh-token", response.headers["location"])
        exchange.assert_awaited_once_with("google-code")
        save.assert_called_once_with(self.db, 42, "test-refresh-token")

    def test_callback_keeps_an_explicit_error_for_invalid_state(self):
        with patch.object(gmail_router, "verify_state", return_value=None):
            response = self.client.get(
                "/api/v1/gmail/callback?code=google-code&state=invalid",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("State OAuth invalide", response.json()["detail"])

    def test_callback_keeps_an_explicit_error_when_consent_is_refused(self):
        with patch.object(gmail_router, "verify_state", return_value=42):
            response = self.client.get(
                "/api/v1/gmail/callback?error=access_denied&state=signed-state",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Consentement refusé", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
