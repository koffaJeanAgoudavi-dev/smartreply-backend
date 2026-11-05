"""Tests de publication et de sûreté de l'interface administrateur."""

import re
import unittest

from fastapi.testclient import TestClient

from app.main import app


class AdminUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_admin_path_redirects_to_private_console_shell(self):
        redirect = self.client.get("/admin", follow_redirects=False)
        page = self.client.get("/admin/")

        self.assertEqual(redirect.status_code, 307)
        self.assertEqual(redirect.headers["location"], "/admin/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn('lang="fr"', page.text)
        self.assertIn("Centre de pilotage", page.text)
        self.assertIn("Code administrateur", page.text)
        self.assertIn("expire automatiquement après 30 minutes", page.text)
        self.assertIn('type="password"', page.text)
        self.assertIn("Vue d’ensemble", page.text)
        self.assertIn("Entreprises", page.text)
        self.assertIn("Incidents opérationnels", page.text)
        self.assertIn("Lecture seule", page.text)
        self.assertIn('content="noindex,nofollow"', page.text)
        self.assertGreaterEqual(page.text.count('class="new-badge"'), 2)

    def test_admin_assets_are_local_responsive_and_use_safe_dom_rendering(self):
        page = self.client.get("/admin/")
        stylesheet = self.client.get("/admin/styles.css")
        javascript = self.client.get("/admin/app.js")

        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn("text/css", stylesheet.headers["content-type"])
        self.assertEqual(javascript.status_code, 200)
        self.assertIn("javascript", javascript.headers["content-type"])
        self.assertIn("@media (max-width: 860px)", stylesheet.text)
        self.assertIn("@media (max-width: 640px)", stylesheet.text)
        self.assertIn("@media (prefers-reduced-motion: reduce)", stylesheet.text)
        self.assertIn(
            'const TOKEN_STORAGE_KEY = "smartreply_access_token"',
            javascript.text,
        )
        for endpoint in (
            "/api/v1/admin/access",
            "/api/v1/admin/unlock",
            "/api/v1/admin/lock",
            "/api/v1/admin/me",
            "/api/v1/admin/overview",
            "/api/v1/admin/companies",
            "/api/v1/admin/incidents",
        ):
            self.assertIn(endpoint, javascript.text)
        self.assertIn("navigator.clipboard.writeText", javascript.text)
        self.assertIn('credentials: "same-origin"', javascript.text)
        self.assertIn("textContent", javascript.text)
        self.assertNotIn("localStorage.setItem", javascript.text)
        self.assertNotIn("innerHTML", javascript.text)
        self.assertNotIn(".style.", javascript.text)
        self.assertNotIn("style=", page.text)
        self.assertNotRegex(
            page.text,
            re.compile(
                r'<(?:script|link|img)[^>]+(?:src|href)=["\']https?://',
                re.IGNORECASE,
            ),
        )

    def test_admin_is_not_linked_from_public_or_user_navigation(self):
        for path in ("/", "/dashboard/", "/contact/"):
            with self.subTest(path=path):
                page = self.client.get(path)
                self.assertEqual(page.status_code, 200)
                self.assertNotIn('href="/admin', page.text)

    def test_admin_markup_never_requests_sensitive_values(self):
        page = self.client.get("/admin/")
        javascript = self.client.get("/admin/app.js")
        combined = page.text + javascript.text

        for forbidden_term in (
            "encrypted_refresh_token",
            "gmail_message_id",
            "gmail_draft_id",
            "telegram_chat_id",
            "callback_token",
            "INVITATION_CODES",
            "PIPELINE_ALLOWED_COMPANY_IDS",
        ):
            self.assertNotIn(forbidden_term, combined)
        self.assertIn(
            "Le contenu des emails, des brouillons et du profil de réponse",
            page.text,
        )


if __name__ == "__main__":
    unittest.main()
