"""Tests du montage statique du dashboard et du retour OAuth Gmail."""

import json
import re
import subprocess
import unittest
from pathlib import Path
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
        self.assertIn('id="register-invitation"', response.text)
        self.assertIn("Code d'invitation", response.text)
        self.assertIn("Indicateurs essentiels", response.text)
        self.assertIn("Emails analysés", response.text)
        self.assertIn("Emails traités", response.text)
        self.assertIn("Emails ignorés", response.text)
        self.assertIn("Brouillons en attente", response.text)
        self.assertIn("Emails récents", response.text)
        self.assertIn("Activité récente", response.text)
        self.assertIn('id="recent-emails-state"', response.text)
        self.assertIn('id="dashboard-empty-state"', response.text)
        self.assertIn('id="dashboard-data-error"', response.text)
        for excluded_feature in ("Plan Pro", "Système actif"):
            self.assertNotIn(excluded_feature, response.text)
        self.assertNotRegex(
            response.text,
            re.compile(
                r'<(?:script|link)[^>]+(?:src|href)=["\']https?://',
                re.IGNORECASE,
            ),
        )

    def test_dashboard_keeps_counter_but_hides_duplicate_integration_section(self):
        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        dashboard_markup = response.text.split(
            '<section id="dashboard-view"',
            maxsplit=1,
        )[1].split('<section id="integrations-view"', maxsplit=1)[0]
        integrations_markup = response.text.split(
            '<section id="integrations-view"',
            maxsplit=1,
        )[1]

        for removed_dashboard_element in (
            "integration-summary-title",
            "dashboard-gmail-status",
            "dashboard-sheet-status",
            "dashboard-telegram-status",
            "Gérer les intégrations",
        ):
            self.assertNotIn(removed_dashboard_element, dashboard_markup)

        for preserved_dashboard_element in (
            'id="dashboard-connected-count"',
            "intégrations connectées",
            'id="metric-analyzed"',
            'id="metric-processed"',
            'id="metric-ignored"',
            'id="metric-pending"',
            'id="volume-chart"',
            'id="category-chart"',
            'id="priority-chart"',
            'id="recent-emails-title"',
            'id="recent-activity-title"',
            'id="dashboard-empty-state"',
        ):
            self.assertIn(preserved_dashboard_element, dashboard_markup)

        self.assertIn('data-route="integrations"', response.text)
        for integration_page_element in (
            'id="integrations-title"',
            'id="readiness-banner"',
            'id="gmail-status"',
            'id="sheet-status"',
            'id="telegram-status"',
            'id="connect-gmail"',
            'id="open-sheet"',
            'id="generate-telegram-code"',
        ):
            self.assertIn(integration_page_element, integrations_markup)

    def test_dashboard_css_and_javascript_are_served(self):
        css_response = self.client.get("/dashboard/styles.css")
        js_response = self.client.get("/dashboard/app.js")

        self.assertEqual(css_response.status_code, 200)
        self.assertIn("text/css", css_response.headers["content-type"])
        self.assertIn("--color-primary-700", css_response.text)
        self.assertIn("--radius-lg", css_response.text)
        self.assertIn("--shadow-card", css_response.text)
        self.assertIn(".donut-chart", css_response.text)
        self.assertIn(".donut-legend", css_response.text)
        self.assertIn(".donut-segment", css_response.text)
        self.assertEqual(js_response.status_code, 200)
        self.assertIn("javascript", js_response.headers["content-type"])
        self.assertIn('"smartreply_access_token"', js_response.text)
        self.assertIn('"/api/v1/gmail/connect"', js_response.text)
        self.assertIn('"/api/v1/telegram/link-code"', js_response.text)
        self.assertIn('"/api/v1/dashboard/overview"', js_response.text)
        self.assertIn('"/api/v1/dashboard/recent-emails"', js_response.text)
        self.assertIn("invitation_code", js_response.text)
        self.assertIn("Code d'invitation invalide.", js_response.text)
        self.assertIn("renderVolumeChart", js_response.text)
        self.assertIn("renderDonutChart", js_response.text)
        self.assertIn("prepareDonutData", js_response.text)
        self.assertIn("document.createElementNS", js_response.text)
        self.assertNotIn("renderBreakdownChart", js_response.text)
        self.assertIn("renderRecentEmails", js_response.text)
        donut_source = js_response.text.split(
            "function renderDonutChart", maxsplit=1
        )[1]
        donut_source = donut_source.split("function renderActivity", maxsplit=1)[0]
        self.assertNotIn("innerHTML", donut_source)
        self.assertIn('setStatus($("gmail-status")', js_response.text)
        self.assertIn('setStatus($("sheet-status")', js_response.text)
        self.assertIn('setStatus($("telegram-status")', js_response.text)
        self.assertIn(
            '$("dashboard-connected-count").textContent = `${count}/3`',
            js_response.text,
        )
        self.assertNotIn("dashboard-gmail-status", js_response.text)
        self.assertNotIn("dashboard-sheet-status", js_response.text)
        self.assertNotIn("dashboard-telegram-status", js_response.text)
        self.assertNotIn("pipeline sera branché à votre compte", js_response.text)

    def test_donut_data_counts_percentages_and_unclassified_values(self):
        repository_root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require("fs");
const vm = require("vm");
let source = fs.readFileSync("app/dashboard/app.js", "utf8");
const marker = '  document.addEventListener("DOMContentLoaded", initialize);\n})();';
if (!source.includes(marker)) throw new Error("Dashboard test export marker missing");
source = source.replace(
  marker,
  '  globalThis.__donutTest = { prepareDonutData };\n})();',
);
const context = {
  window: { localStorage: { getItem: () => null } },
  console,
  Intl,
};
vm.createContext(context);
vm.runInContext(source, context);
const prepare = context.__donutTest.prepareDonutData;
const result = {
  categories: prepare([
    { key: "support", label: "Support", count: 4 },
    { key: "spam", label: "Spam", count: 2 },
    { key: "newsletter", label: "Newsletter", count: 0 },
    { key: "autre", label: "Autre", count: null },
  ], 10),
  priorities: prepare([
    { key: "haute", label: "Haute", count: 3 },
    { key: "moyenne", label: "Moyenne", count: 2 },
    { key: "basse", label: "Basse", count: 3 },
  ], 10),
  thirds: prepare([
    { key: "devis", label: "Devis", count: 1 },
    { key: "support", label: "Support", count: 1 },
    { key: "autre", label: "Autre", count: 1 },
  ], 3),
  empty: prepare([], 0),
  missingItems: prepare(null, 4),
  nullValues: prepare([{ key: "support", label: "Support", count: null }], 0),
  inconsistent: prepare([{ key: "support", label: "Support", count: 5 }], 4),
  negative: prepare([{ key: "support", label: "Support", count: -1 }], 4),
  negativeTotal: prepare([], -1),
  fractional: prepare([{ key: "support", label: "Support", count: 1.5 }], 4),
};
process.stdout.write(JSON.stringify(result));
'''
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)

        categories = result["categories"]
        self.assertEqual(categories["state"], "ready")
        self.assertEqual(categories["total"], 10)
        self.assertEqual(
            [
                (item["key"], item["label"], item["count"])
                for item in categories["segments"]
            ],
            [
                ("support", "Support", 4),
                ("spam", "Spam", 2),
                ("unclassified", "Non classé", 4),
            ],
        )
        self.assertEqual(
            [item["percentage"] for item in categories["segments"]],
            [40, 20, 40],
        )
        self.assertEqual(
            [item["share"] for item in categories["segments"]],
            [0.4, 0.2, 0.4],
        )

        priorities = result["priorities"]
        self.assertEqual(priorities["state"], "ready")
        self.assertEqual(
            [(item["key"], item["count"]) for item in priorities["segments"]],
            [("haute", 3), ("moyenne", 2), ("basse", 3), ("unclassified", 2)],
        )
        self.assertEqual(
            [item["percentage"] for item in priorities["segments"]],
            [30, 20, 30, 20],
        )

        self.assertAlmostEqual(
            sum(item["percentage"] for item in result["thirds"]["segments"]),
            100,
        )
        self.assertEqual(result["empty"]["state"], "empty")
        self.assertEqual(result["nullValues"]["state"], "empty")
        self.assertEqual(result["missingItems"]["state"], "ready")
        self.assertEqual(
            result["missingItems"]["segments"],
            [
                {
                    "key": "unclassified",
                    "label": "Non classé",
                    "count": 4,
                    "percentage": 100,
                    "share": 1,
                }
            ],
        )
        self.assertEqual(result["inconsistent"]["state"], "inconsistent")
        self.assertEqual(result["negative"]["state"], "inconsistent")
        self.assertEqual(result["negativeTotal"]["state"], "inconsistent")
        self.assertEqual(result["fractional"]["state"], "inconsistent")

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
