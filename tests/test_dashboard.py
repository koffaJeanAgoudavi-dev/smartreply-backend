"""Tests du montage statique du dashboard et du retour OAuth Gmail."""

import json
import re
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urljoin

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
        self.assertEqual(response.headers["location"], "/dashboard/")

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

    def test_telegram_code_instructions_link_to_the_bot_safely(self):
        response = self.client.get("/dashboard/")
        js_response = self.client.get("/dashboard/app.js")

        self.assertEqual(response.status_code, 200)
        telegram_panel = response.text.split('id="telegram-code-panel"', maxsplit=1)[
            1
        ].split("</article>", maxsplit=1)[0]
        self.assertIn(
            (
                "Ouvrez le bot SmartReply dans Telegram : "
                '<a href="https://t.me/SmartReply_Agent_Bot" target="_blank" '
                'rel="noopener noreferrer">Ouvrir le bot Telegram</a>'
            ),
            telegram_panel,
        )
        for preserved_element in (
            'id="telegram-code"',
            'id="copy-telegram-code"',
            'id="telegram-command"',
            'id="telegram-expiry"',
            "Cette page vérifiera automatiquement la connexion.",
        ):
            self.assertIn(preserved_element, telegram_panel)
        self.assertIn(
            '$("telegram-command").textContent = `/start ${response.code}`',
            js_response.text,
        )
        self.assertIn("startTelegramPolling(response.expires_at)", js_response.text)
        self.assertIn(
            '$("copy-telegram-code").addEventListener("click", copyTelegramCode)',
            js_response.text,
        )

    def test_integration_setup_dialog_is_accessible_and_concise(self):
        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        dialog_markup = response.text.split(
            'id="integration-setup-dialog"', maxsplit=1
        )[1].split("</dialog>", maxsplit=1)[0]
        for accessibility_attribute in (
            'role="dialog"',
            'aria-modal="true"',
            'aria-labelledby="integration-setup-title"',
            'aria-describedby="integration-setup-description"',
            'aria-label="Fermer cette fenêtre"',
        ):
            self.assertIn(accessibility_attribute, dialog_markup)
        for expected_copy in (
            "Configuration requise",
            "Configurez vos intégrations",
            "Gmail",
            "Telegram",
            "Google Sheets",
            "Configurer mes intégrations",
        ):
            self.assertIn(expected_copy, dialog_markup)
        for integration_card in (
            "gmail-integration-card",
            "sheet-integration-card",
            "telegram-integration-card",
        ):
            self.assertIn(f'id="{integration_card}"', response.text)
        opening_tag = response.text.split("<dialog", maxsplit=1)[1].split(
            ">", maxsplit=1
        )[0]
        self.assertNotIn("open", opening_tag)

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
        self.assertIn(".integration-setup-dialog::backdrop", css_response.text)
        self.assertIn(".integration-card.needs-configuration", css_response.text)
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
        donut_source = js_response.text.split("function renderDonutChart", maxsplit=1)[
            1
        ]
        donut_source = donut_source.split("function renderActivity", maxsplit=1)[0]
        self.assertNotIn("innerHTML", donut_source)
        self.assertIn('setStatus($("gmail-status")', js_response.text)
        self.assertIn('setStatus($("sheet-status")', js_response.text)
        self.assertIn('setStatus($("telegram-status")', js_response.text)
        self.assertIn(
            '$("dashboard-connected-count").textContent = countLabel',
            js_response.text,
        )
        self.assertIn(
            'integrations.statusesAvailable ? `${count}/3` : "—/3"',
            js_response.text,
        )
        self.assertIn("Vérification temporairement indisponible", js_response.text)
        self.assertNotIn("dashboard-gmail-status", js_response.text)
        self.assertNotIn("dashboard-sheet-status", js_response.text)
        self.assertNotIn("dashboard-telegram-status", js_response.text)
        self.assertNotIn("pipeline sera branché à votre compte", js_response.text)
        self.assertIn("evaluateIntegrationSetup", js_response.text)
        self.assertIn("maybeShowIntegrationSetupModal", js_response.text)
        self.assertIn('typeof dialog.showModal !== "function"', js_response.text)
        self.assertIn('window.location.hash = "integrations"', js_response.text)
        self.assertIn("state.integrationSetup.dismissed = true", js_response.text)
        self.assertIn(
            "state.integrations.statusesAvailable = statusesAvailable",
            js_response.text,
        )

    def test_integration_setup_decision_covers_status_and_visit_scenarios(self):
        repository_root = Path(__file__).resolve().parents[1]
        script = r"""
const fs = require("fs");
const vm = require("vm");
let source = fs.readFileSync("app/dashboard/app.js", "utf8");
const marker = '  document.addEventListener("DOMContentLoaded", initialize);\n})();';
if (!source.includes(marker)) throw new Error("Dashboard test export marker missing");
source = source.replace(
  marker,
  `  globalThis.__integrationSetupTest = {
    checkIntegrationStatuses,
    evaluateIntegrationSetup,
    loadDashboardResourcesIndependently,
    maybeShowIntegrationSetupModal,
    navigateToIntegrationSetup,
    state,
  };\n})();`,
);
const highlighted = {};
const card = (key) => ({
  classList: {
    toggle: (_className, active) => { highlighted[key] = active; },
  },
});
const elements = {
  "integration-setup-dialog": {
    open: false,
    showModal() { this.open = true; },
    close() { this.open = false; },
  },
  "configure-integrations": {
    focused: false,
    focus() { this.focused = true; },
  },
  "gmail-integration-card": card("gmail"),
  "sheet-integration-card": card("sheet"),
  "telegram-integration-card": card("telegram"),
};
const context = {
  window: {
    localStorage: { getItem: () => null },
    location: { hash: "#dashboard" },
  },
  document: { getElementById: (id) => elements[id] || null },
  console,
  Intl,
  Set,
};
vm.createContext(context);
vm.runInContext(source, context);
const integrationSetup = context.__integrationSetupTest;
const evaluate = integrationSetup.evaluateIntegrationSetup;
const options = {
  dismissed: false,
  route: "dashboard",
};
const result = {
  noneConfigured: evaluate(
    { gmail: false, sheet: false, telegram: false },
    options,
  ),
  oneMissing: evaluate(
    { gmail: true, sheet: false, telegram: true },
    options,
  ),
  multipleMissing: evaluate(
    { gmail: false, sheet: true, telegram: false },
    options,
  ),
  allConfigured: evaluate(
    { gmail: true, sheet: true, telegram: true },
    options,
  ),
  dismissed: evaluate(
    { gmail: false, sheet: true, telegram: true },
    { ...options, dismissed: true },
  ),
  newLogin: evaluate(
    { gmail: false, sheet: true, telegram: true },
    { ...options, dismissed: false },
  ),
  integrationsRoute: evaluate(
    { gmail: false, sheet: true, telegram: true },
    { ...options, route: "integrations" },
  ),
};

(async () => {
  integrationSetup.state.appReady = true;
  integrationSetup.state.token = "authenticated";
  const missingSnapshot = {
    gmail: false,
    sheet: true,
    telegram: false,
  };
  await integrationSetup.checkIntegrationStatuses({}, async () => null);
  result.statusFailureSuppressed = !elements["integration-setup-dialog"].open;
  await integrationSetup.checkIntegrationStatuses(
    {},
    async () => missingSnapshot,
  );
  result.successfulRetryOpened = elements["integration-setup-dialog"].open;
  elements["integration-setup-dialog"].close();
  await integrationSetup.checkIntegrationStatuses(
    {},
    async () => ({ gmail: true, sheet: true, telegram: true }),
  );
  result.allConfiguredStayedClosed = !elements["integration-setup-dialog"].open;
  await integrationSetup.checkIntegrationStatuses(
    {},
    async () => missingSnapshot,
  );
  result.modalAction = {
    opened: elements["integration-setup-dialog"].open,
    focused: elements["configure-integrations"].focused,
    missing: integrationSetup.state.integrationSetup.missing,
  };
  integrationSetup.navigateToIntegrationSetup();
  result.modalAction.closed = !elements["integration-setup-dialog"].open;
  result.modalAction.dismissed = integrationSetup.state.integrationSetup.dismissed;
  result.modalAction.hash = context.window.location.hash;
  result.modalAction.highlighted = highlighted;
  await integrationSetup.checkIntegrationStatuses(
    {},
    async () => missingSnapshot,
  );
  result.modalAction.reopenedAfterDismissal = elements["integration-setup-dialog"].open;
  process.stdout.write(JSON.stringify(result));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)

        self.assertTrue(result["noneConfigured"]["shouldShow"])
        self.assertEqual(
            result["noneConfigured"]["missing"],
            ["gmail", "sheet", "telegram"],
        )
        self.assertTrue(result["oneMissing"]["shouldShow"])
        self.assertEqual(result["oneMissing"]["missing"], ["sheet"])
        self.assertTrue(result["multipleMissing"]["shouldShow"])
        self.assertEqual(result["multipleMissing"]["missing"], ["gmail", "telegram"])
        self.assertFalse(result["allConfigured"]["shouldShow"])
        self.assertEqual(result["allConfigured"]["missing"], [])
        self.assertFalse(result["dismissed"]["shouldShow"])
        self.assertTrue(result["newLogin"]["shouldShow"])
        self.assertFalse(result["integrationsRoute"]["shouldShow"])
        self.assertTrue(result["statusFailureSuppressed"])
        self.assertTrue(result["successfulRetryOpened"])
        self.assertTrue(result["allConfiguredStayedClosed"])
        self.assertEqual(
            result["modalAction"],
            {
                "opened": True,
                "focused": True,
                "missing": ["gmail", "telegram"],
                "closed": True,
                "dismissed": True,
                "hash": "integrations",
                "highlighted": {
                    "gmail": True,
                    "sheet": False,
                    "telegram": True,
                },
                "reopenedAfterDismissal": False,
            },
        )

    def test_integration_statuses_complete_before_slow_or_failed_analytics(self):
        repository_root = Path(__file__).resolve().parents[1]
        script = r"""
const fs = require("fs");
const vm = require("vm");
let source = fs.readFileSync("app/dashboard/app.js", "utf8");
const marker = '  document.addEventListener("DOMContentLoaded", initialize);\n})();';
if (!source.includes(marker)) throw new Error("Dashboard test export marker missing");
source = source.replace(
  marker,
  `  globalThis.__dashboardLoadTest = {
    checkIntegrationStatuses,
    loadDashboardResourcesIndependently,
    state,
  };\n})();`,
);
const elements = {
  "integration-setup-dialog": {
    open: false,
    showModal() { this.open = true; },
    close() { this.open = false; },
  },
  "configure-integrations": { focus() {} },
};
const context = {
  window: {
    localStorage: { getItem: () => null },
    location: { hash: "#dashboard" },
  },
  document: { getElementById: (id) => elements[id] || null },
  console,
  Intl,
};
vm.createContext(context);
vm.runInContext(source, context);
const dashboardLoad = context.__dashboardLoadTest;
const missingSnapshot = { gmail: false, sheet: true, telegram: true };
dashboardLoad.state.appReady = true;
dashboardLoad.state.token = "authenticated";

(async () => {
  let resolveAnalytics;
  let slowLoadFinished = false;
  const slowLoad = dashboardLoad.loadDashboardResourcesIndependently(
    () => dashboardLoad.checkIntegrationStatuses(
      {},
      async () => missingSnapshot,
    ),
    () => new Promise((resolve) => { resolveAnalytics = resolve; }),
  ).then(() => { slowLoadFinished = true; });
  await new Promise((resolve) => setImmediate(resolve));
  const beforeAnalytics = {
    modalOpen: elements["integration-setup-dialog"].open,
    slowLoadFinished,
  };
  resolveAnalytics();
  await slowLoad;

  elements["integration-setup-dialog"].close();
  await dashboardLoad.loadDashboardResourcesIndependently(
    () => dashboardLoad.checkIntegrationStatuses(
      {},
      async () => missingSnapshot,
    ),
    async () => { throw new Error("analytics unavailable"); },
  );

  process.stdout.write(JSON.stringify({
    beforeAnalytics,
    slowLoadFinished,
    modalOpenAfterAnalyticsFailure: elements["integration-setup-dialog"].open,
  }));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)

        self.assertTrue(result["beforeAnalytics"]["modalOpen"])
        self.assertFalse(result["beforeAnalytics"]["slowLoadFinished"])
        self.assertTrue(result["slowLoadFinished"])
        self.assertTrue(result["modalOpenAfterAnalyticsFailure"])

    def test_integration_setup_uses_authenticated_status_sources_only(self):
        js_response = self.client.get("/dashboard/app.js")

        self.assertEqual(js_response.status_code, 200)
        status_loader = js_response.text.split(
            "async function loadIntegrationStatuses", maxsplit=1
        )[1].split("function formatCount", maxsplit=1)[0]
        self.assertIn("apiRequest(API.gmailStatus)", status_loader)
        self.assertIn("apiRequest(API.telegramStatus)", status_loader)
        self.assertIn('gmailResult.status === "fulfilled"', status_loader)
        self.assertIn('telegramResult.status === "fulfilled"', status_loader)
        self.assertIn("if (!statusesAvailable) return null", status_loader)
        self.assertIn("return Object.freeze", status_loader)
        self.assertIn("Boolean(state.company && state.company.sheet_id)", status_loader)
        self.assertNotIn("company_id", status_loader)
        self.assertIn('company: "/api/v1/companies/me"', js_response.text)

        status_check = js_response.text.split(
            "async function checkIntegrationStatuses", maxsplit=1
        )[1].split("function formatCount", maxsplit=1)[0]
        self.assertIn("maybeShowIntegrationSetupModal(statusSnapshot)", status_check)

        entry_source = js_response.text.split(
            "async function enterDashboard", maxsplit=1
        )[1].split("function renderIdentity", maxsplit=1)[0]
        self.assertIn("loadDashboardResourcesIndependently", entry_source)
        self.assertNotIn("Promise.allSettled", entry_source)

        refresh_source = js_response.text.split(
            "async function refreshDashboard", maxsplit=1
        )[1].split("async function connectGmail", maxsplit=1)[0]
        self.assertIn("loadDashboardResourcesIndependently", refresh_source)

    def test_integration_setup_dismissal_resets_with_the_authenticated_session(self):
        js_response = self.client.get("/dashboard/app.js")

        set_session_source = js_response.text.split("function setSession", maxsplit=1)[
            1
        ].split("function clearSession", maxsplit=1)[0]
        clear_session_source = js_response.text.split(
            "function clearSession", maxsplit=1
        )[1].split("function logout", maxsplit=1)[0]
        for source in (set_session_source, clear_session_source):
            self.assertIn("state.integrationSetup", source)
            self.assertIn("dismissed: false", source)
            self.assertIn("missing: []", source)

    def test_donut_data_counts_percentages_and_unclassified_values(self):
        repository_root = Path(__file__).resolve().parents[1]
        script = r"""
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
"""
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
        app.dependency_overrides[gmail_router.get_current_user] = lambda: (
            SimpleNamespace(id=42)
        )
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

    def test_gmail_status_is_isolated_by_authenticated_user(self):
        authenticated_user = SimpleNamespace(id=42)
        app.dependency_overrides[gmail_router.get_current_user] = lambda: (
            authenticated_user
        )
        with patch.object(
            gmail_router.service,
            "is_connected",
            side_effect=lambda _db, user_id: user_id == 42,
        ) as is_connected:
            first_response = self.client.get("/api/v1/gmail/status")
            authenticated_user.id = 84
            second_response = self.client.get("/api/v1/gmail/status")

        self.assertEqual(first_response.json(), {"connected": True})
        self.assertEqual(second_response.json(), {"connected": False})
        self.assertEqual(
            [call.args[1] for call in is_connected.call_args_list], [42, 84]
        )

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
        self.assertEqual(
            urljoin("https://smartreply.example/", response.headers["location"]),
            "https://smartreply.example/dashboard/?gmail=connected#integrations",
        )
        self.assertNotIn("test-refresh-token", response.headers["location"])
        exchange.assert_awaited_once_with("google-code")
        save.assert_called_once_with(self.db, 42, "test-refresh-token")

    def test_deleted_user_callback_stops_before_google_exchange_or_token_save(self):
        self.db.get.return_value = None
        with (
            patch.object(gmail_router, "verify_state", return_value=42),
            patch.object(
                gmail_router.service,
                "exchange_code_for_refresh_token",
                new=AsyncMock(),
            ) as exchange,
            patch.object(gmail_router.service, "save_refresh_token") as save,
        ):
            response = self.client.get(
                "/api/v1/gmail/callback?code=google-code&state=signed-state",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Utilisateur introuvable"})
        exchange.assert_not_awaited()
        save.assert_not_called()

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
