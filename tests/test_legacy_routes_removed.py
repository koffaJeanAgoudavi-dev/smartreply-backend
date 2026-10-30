"""Les anciennes routes techniques ne font plus partie de l'API publique."""

import unittest

from fastapi.testclient import TestClient

from app.main import app


class LegacyRoutesRemovedTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_legacy_technical_routes_return_404(self):
        routes = (
            ("GET", "/api/v1/gmail/test"),
            ("GET", "/api/v1/filter/test"),
            ("POST", "/api/v1/ai/test"),
            ("POST", "/api/v1/sheets/test"),
            ("POST", "/api/v1/drafts/test"),
            ("POST", "/api/v1/notifications/test"),
            ("POST", "/api/v1/actions/test-ignore"),
            ("POST", "/api/v1/actions/test-send"),
        )

        for method, path in routes:
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path)
                self.assertEqual(response.status_code, 404, response.text)

    def test_new_public_routes_remain_registered(self):
        paths = set(app.openapi()["paths"])
        self.assertIn("/api/v1/pipeline/run", paths)
        self.assertIn("/api/v1/gmail/connect", paths)
        self.assertIn("/api/v1/gmail/callback", paths)
        self.assertIn("/api/v1/gmail/status", paths)
        self.assertIn("/api/v1/companies", paths)
        self.assertIn("/api/v1/telegram/webhook", paths)
        self.assertIn("/api/v1/dashboard/overview", paths)
        self.assertIn("/api/v1/dashboard/recent-emails", paths)
        mounted_paths = {
            route.path
            for route in app.routes
            if getattr(route, "path", None)
        }
        self.assertIn("/dashboard", mounted_paths)


if __name__ == "__main__":
    unittest.main()
