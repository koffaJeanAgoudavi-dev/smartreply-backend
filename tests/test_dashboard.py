"""Tests du montage statique du dashboard et du retour OAuth Gmail."""

import json
import re
import struct
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
        self.assertIn('href="#drafts" data-route="drafts"', response.text)
        self.assertNotIn("Bientôt", response.text)
        self.assertIn('id="register-invitation"', response.text)
        self.assertIn("Code d'invitation", response.text)
        self.assertIn('id="connect-drive-onboarding"', response.text)
        self.assertIn('id="onboarding-drive-status"', response.text)
        self.assertIn('id="company-submit"', response.text)
        self.assertIn("restera votre propriété", response.text)
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

    def test_new_badges_remain_visible_on_emails_drafts_and_settings(self):
        response = self.client.get("/dashboard/")
        stylesheet = self.client.get("/dashboard/styles.css")
        sidebar = response.text.split(
            '<nav class="primary-nav">',
            maxsplit=1,
        )[1].split("</nav>", maxsplit=1)[0]

        self.assertEqual(sidebar.count('class="nav-badge-new"'), 3)
        for route, label in (
            ("emails", "Emails"),
            ("drafts", "Brouillons"),
            ("settings", "Paramètres"),
        ):
            with self.subTest(route=route):
                link = sidebar.split(
                    f'href="#{route}" data-route="{route}">',
                    maxsplit=1,
                )[1].split("</a>", maxsplit=1)[0]
                self.assertIn(f"<span>{label}</span>", link)
                self.assertIn(
                    '<small class="nav-badge-new">Nouveau</small>',
                    link,
                )

        for route in ("dashboard", "integrations"):
            link = sidebar.split(
                f'href="#{route}" data-route="{route}">',
                maxsplit=1,
            )[1].split("</a>", maxsplit=1)[0]
            self.assertNotIn("nav-badge-new", link)
        self.assertIn(".nav-item .nav-badge-new", stylesheet.text)
        self.assertIn(".nav-item.active .nav-badge-new", stylesheet.text)
        badge_styles = stylesheet.text.split(
            ".nav-item .nav-badge-new",
            maxsplit=1,
        )[1].split(".sidebar-divider", maxsplit=1)[0]
        self.assertIn("var(--color-success-soft)", badge_styles)
        self.assertIn("var(--color-success)", badge_styles)
        self.assertNotIn("#fff3c4", badge_styles)
        self.assertNotIn("dismiss-new-badge", response.text)

    def test_dashboard_serves_official_brand_assets_and_metadata(self):
        response = self.client.get("/dashboard/")
        standard_path = "/dashboard/assets/brand/smartreply-mark-standard.png"
        inverse_path = "/dashboard/assets/brand/smartreply-mark-inverse.png"

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count(f'src="{standard_path}"'), 4)
        self.assertEqual(response.text.count(f'src="{inverse_path}"'), 1)
        self.assertNotIn('id="icon-logo"', response.text)
        for metadata in (
            (
                '<link rel="icon" href="/dashboard/assets/brand/favicon.ico" '
                'sizes="any">'
            ),
            (
                '<link rel="icon" type="image/png" sizes="32x32" '
                'href="/dashboard/assets/brand/favicon-32x32.png">'
            ),
            (
                '<link rel="icon" type="image/png" sizes="48x48" '
                'href="/dashboard/assets/brand/favicon-48x48.png">'
            ),
            (
                '<link rel="apple-touch-icon" sizes="180x180" '
                'href="/dashboard/assets/brand/apple-touch-icon.png">'
            ),
        ):
            self.assertIn(metadata, response.text)

        png_expectations = {
            standard_path: (245, 245, 6),
            inverse_path: (245, 245, 6),
            "/dashboard/assets/brand/favicon-32x32.png": (32, 32, 6),
            "/dashboard/assets/brand/favicon-48x48.png": (48, 48, 6),
            "/dashboard/assets/brand/apple-touch-icon.png": (180, 180, 2),
        }
        png_responses = {}
        for path, (expected_width, expected_height, expected_color_type) in (
            png_expectations.items()
        ):
            with self.subTest(path=path):
                asset = self.client.get(path)
                png_responses[path] = asset
                self.assertEqual(asset.status_code, 200)
                self.assertIn("image/png", asset.headers["content-type"])
                self.assertEqual(asset.content[:8], b"\x89PNG\r\n\x1a\n")
                width, height = struct.unpack(">II", asset.content[16:24])
                self.assertEqual((width, height), (expected_width, expected_height))
                self.assertEqual(asset.content[24], 8)
                self.assertEqual(asset.content[25], expected_color_type)

        icon = self.client.get("/dashboard/assets/brand/favicon.ico")
        self.assertEqual(icon.status_code, 200)
        self.assertTrue(icon.headers["content-type"].startswith("image/"))
        self.assertEqual(struct.unpack_from("<HHH", icon.content), (0, 1, 3))
        for index, expected_size in enumerate((16, 32, 48)):
            entry = struct.unpack_from("<BBBBHHII", icon.content, 6 + 16 * index)
            width, height, _, _, planes, bits, payload_size, offset = entry
            self.assertEqual((width, height), (expected_size, expected_size))
            self.assertEqual((planes, bits), (1, 32))
            payload = icon.content[offset : offset + payload_size]
            self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(
                struct.unpack(">II", payload[16:24]),
                (expected_size, expected_size),
            )
            if expected_size in (32, 48):
                served_png = png_responses[
                    f"/dashboard/assets/brand/favicon-{expected_size}x{expected_size}.png"
                ]
                self.assertEqual(payload, served_png.content)

    def test_contact_is_linked_from_auth_and_footer_but_not_sidebar(self):
        response = self.client.get("/dashboard/")
        stylesheet = self.client.get("/dashboard/styles.css")
        public_contact = self.client.get("/contact/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count('href="/contact/"'), 4)
        auth_markup = response.text.split(
            '<main id="auth-screen"', maxsplit=1
        )[1].split('<main id="company-screen"', maxsplit=1)[0]
        sidebar_markup = response.text.split(
            '<aside id="sidebar"', maxsplit=1
        )[1].split("</aside>", maxsplit=1)[0]
        dashboard_footer = response.text.split(
            '<footer class="dashboard-footer">', maxsplit=1
        )[1].split("</footer>", maxsplit=1)[0]

        self.assertIn(
            '<a class="auth-contact-link" href="/contact/">Contact</a>',
            auth_markup,
        )
        self.assertNotIn('href="/contact/"', sidebar_markup)
        self.assertNotIn("Contact", sidebar_markup)
        self.assertIn('<a href="/contact/">Contact</a>', dashboard_footer)
        self.assertEqual(public_contact.status_code, 200)
        self.assertIn("Contactez l’équipe SmartReply Agent", public_contact.text)

        self.assertEqual(stylesheet.status_code, 200)
        for responsive_style in (
            ".auth-panel-footer",
            ".auth-contact-link",
            ".dashboard-footer",
            "overflow-y: auto",
            "@media (min-width: 540px)",
            "@media (min-width: 920px)",
        ):
            self.assertIn(responsive_style, stylesheet.text)

    def test_conditions_are_linked_from_registration_settings_and_footer(self):
        response = self.client.get("/dashboard/")
        stylesheet = self.client.get("/dashboard/styles.css")
        public_conditions = self.client.get("/conditions/")
        public_privacy = self.client.get("/confidentialite/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count('href="/conditions/"'), 3)
        self.assertEqual(response.text.count('href="/confidentialite/"'), 3)
        register_markup = response.text.split(
            '<form id="register-form"', maxsplit=1
        )[1].split("</form>", maxsplit=1)[0]
        settings_markup = response.text.split(
            '<section id="settings-view"', maxsplit=1
        )[1].split("</main>", maxsplit=1)[0]
        dashboard_footer = response.text.split(
            '<footer class="dashboard-footer">', maxsplit=1
        )[1].split("</footer>", maxsplit=1)[0]

        self.assertIn(
            "En créant votre compte, vous reconnaissez avoir pris connaissance",
            register_markup,
        )
        self.assertIn('href="/conditions/"', register_markup)
        self.assertIn('href="/confidentialite/"', register_markup)
        self.assertNotIn('type="checkbox"', register_markup)
        self.assertIn("Conditions générales d’utilisation", settings_markup)
        self.assertIn('href="/conditions/"', settings_markup)
        self.assertIn("Politique de confidentialité", settings_markup)
        self.assertIn('href="/confidentialite/"', settings_markup)
        self.assertIn(
            '<a href="/conditions/">Conditions générales d’utilisation</a>',
            dashboard_footer,
        )
        self.assertIn(
            '<a href="/confidentialite/">Politique de confidentialité</a>',
            dashboard_footer,
        )
        self.assertEqual(public_conditions.status_code, 200)
        self.assertIn("Conditions générales d’utilisation", public_conditions.text)
        self.assertEqual(public_privacy.status_code, 200)
        self.assertIn("Politique de confidentialité", public_privacy.text)
        self.assertIn(".auth-legal a", stylesheet.text)
        self.assertIn("grid-template-columns: repeat(2", stylesheet.text)

    def test_emails_navigation_and_view_structure_are_ready_for_api_data(self):
        response = self.client.get("/dashboard/")
        js_response = self.client.get("/dashboard/app.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '<a class="nav-item" href="#emails" data-route="emails">',
            response.text,
        )
        emails_markup = response.text.split(
            '<section id="emails-view"',
            maxsplit=1,
        )[1].split('<section id="integrations-view"', maxsplit=1)[0]
        for expected_element in (
            'aria-labelledby="emails-title"',
            'id="emails-title"',
            'id="emails-search"',
            'id="emails-category-filter"',
            'id="emails-priority-filter"',
            'id="emails-status-filter"',
            'id="emails-loading-state"',
            'id="emails-empty-state"',
            'id="emails-error-state"',
            'id="emails-retry"',
            'id="emails-table-wrap"',
            'id="emails-table-body"',
            'id="emails-pagination"',
            'id="emails-previous-page"',
            'id="emails-page-indicator"',
            'id="emails-next-page"',
            'id="email-detail-workspace"',
            'id="email-detail-back"',
            'id="email-detail-loading-state"',
            'id="email-detail-not-found-state"',
            'id="email-detail-error-state"',
            'id="email-detail-content"',
            'id="email-detail-sender-name"',
            'id="email-detail-sender-email"',
            'id="email-detail-subject"',
            'id="email-detail-received-at"',
            'id="email-detail-summary"',
            'id="email-detail-category"',
            'id="email-detail-priority"',
            'id="email-detail-status"',
        ):
            self.assertIn(expected_element, emails_markup)
        for column in (
            "Expéditeur",
            "Objet",
            "Date",
            "Catégorie",
            "Priorité",
            "Statut",
        ):
            self.assertIn(f"<th>{column}</th>", emails_markup)
        for detail_label in (
            "Expéditeur",
            "Adresse email",
            "Objet",
            "Date de réception",
            "Résumé",
            "Catégorie",
            "Priorité",
            "Statut",
        ):
            self.assertIn(f"<dt>{detail_label}</dt>", emails_markup)
        self.assertIn("Retour aux emails", emails_markup)
        for forbidden_detail in (
            "company_id",
            "gmail_id",
            "thread_id",
            "Corps original",
        ):
            self.assertNotIn(forbidden_detail, emails_markup)
        self.assertIn('maxlength="200"', emails_markup)
        for allowed_filter_value in (
            "Devis",
            "Support",
            "Facturation",
            "Partenariat",
            "Spam",
            "Newsletter",
            "Autre",
            "Haute",
            "Moyenne",
            "Basse",
            "Analysé",
            "Brouillon prêt",
            "Envoyé",
            "Ignoré",
        ):
            self.assertIn(f'value="{allowed_filter_value}"', emails_markup)
        self.assertIn('id="recent-emails-title"', response.text)
        self.assertIn('id="recent-emails-body"', response.text)
        self.assertIn("cacheEmailsViewElements", js_response.text)
        self.assertIn(
            'dashboardEmails: "/api/v1/dashboard/emails"',
            js_response.text,
        )
        self.assertIn(
            'detailBack.addEventListener("click", returnToEmailList)',
            js_response.text,
        )
        self.assertNotIn('row.setAttribute("role", "link")', js_response.text)
        self.assertIn('document.createElement("button")', js_response.text)

    def test_drafts_tab_is_a_review_workspace_without_direct_sending(self):
        response = self.client.get("/dashboard/")
        stylesheet = self.client.get("/dashboard/styles.css")
        javascript = self.client.get("/dashboard/app.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '<a class="nav-item" href="#drafts" data-route="drafts">',
            response.text,
        )
        drafts_markup = response.text.split(
            '<section id="drafts-view"',
            maxsplit=1,
        )[1].split('<section id="integrations-view"', maxsplit=1)[0]
        for expected_element in (
            'aria-labelledby="drafts-title"',
            'id="drafts-title"',
            'id="refresh-drafts"',
            'id="drafts-gmail-disconnected"',
            'id="draft-metric-pending"',
            'id="draft-metric-sent"',
            'id="draft-metric-ignored"',
            'id="draft-metric-errors"',
            'id="drafts-search"',
            'id="drafts-status-filter"',
            'id="drafts-loading-state"',
            'id="drafts-empty-state"',
            'id="drafts-error-state"',
            'id="drafts-table-body"',
            'id="drafts-pagination"',
            'id="draft-detail-workspace"',
            'id="draft-detail-content"',
            'id="draft-content-preview"',
            'id="copy-draft-content"',
            'id="ignore-draft"',
            'id="draft-ignore-dialog"',
        ):
            self.assertIn(expected_element, drafts_markup)
        for indicator in ("En attente", "Envoyés", "Ignorés", "En erreur"):
            self.assertIn(indicator, drafts_markup)
        for detail_label in (
            "Destinataire",
            "Adresse email",
            "Objet",
            "Email reçu le",
            "Brouillon préparé le",
            "Catégorie",
            "Priorité",
            "Statut",
            "Résumé de l’email",
        ):
            self.assertIn(f"<dt>{detail_label}</dt>", drafts_markup)
        self.assertIn("généré avec l’aide de l’IA", drafts_markup)
        self.assertIn("Aucun message n’est envoyé depuis cet écran", drafts_markup)
        self.assertIn("Copier le contenu", drafts_markup)
        self.assertIn("Ignorer ce brouillon ?", drafts_markup)
        self.assertIn("Ouvrir Intégrations", drafts_markup)
        self.assertIn(
            'href="https://mail.google.com/mail/u/0/#drafts"',
            drafts_markup,
        )
        self.assertIn('target="_blank" rel="noopener noreferrer"', drafts_markup)
        self.assertNotRegex(
            drafts_markup,
            re.compile(r"<button[^>]*>\s*Envoyer", re.IGNORECASE),
        )
        self.assertNotIn("Modifier le brouillon", drafts_markup)
        self.assertNotIn("Régénérer", drafts_markup)
        self.assertIn(".draft-content-preview", stylesheet.text)
        self.assertIn(
            'dashboardDrafts: "/api/v1/dashboard/drafts"',
            javascript.text,
        )
        self.assertIn("navigator.clipboard.writeText(content)", javascript.text)
        self.assertIn("openDraftIgnoreDialog", javascript.text)
        self.assertNotIn("sendDraftFromDashboard", javascript.text)

    def test_dashboard_keeps_counter_but_hides_duplicate_integration_section(self):
        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        dashboard_markup = response.text.split(
            '<section id="dashboard-view"',
            maxsplit=1,
        )[1].split('<section id="emails-view"', maxsplit=1)[0]
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
            'id="connect-drive"',
            'id="repair-sheet-access"',
            'id="disconnect-drive"',
            'id="generate-telegram-code"',
        ):
            self.assertIn(integration_page_element, integrations_markup)
        self.assertIn(
            '<span class="optional-badge">Optionnel</span>',
            integrations_markup,
        )
        self.assertIn("Telegram est optionnel", integrations_markup)

    def test_settings_page_matches_the_selected_first_scope(self):
        response = self.client.get("/dashboard/")
        js_response = self.client.get("/dashboard/app.js")
        css_response = self.client.get("/dashboard/styles.css")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '<a class="nav-item" href="#settings" data-route="settings">',
            response.text,
        )
        settings_markup = response.text.split(
            '<section id="settings-view"', maxsplit=1
        )[1].split("</main>", maxsplit=1)[0]
        for expected in (
            'id="settings-title"',
            'id="settings-company-name"',
            'id="settings-sector"',
            'id="settings-sector-other"',
            'id="settings-profile-title"',
            'class="feature-badge-new">Nouveau</small>',
            'id="settings-business-description"',
            'id="settings-products-services"',
            'id="settings-business-hours"',
            'id="settings-response-signature"',
            'id="settings-business-rules"',
            'id="settings-response-instructions"',
            'id="settings-tone-title"',
            'name="response_tone" value="professional"',
            'name="response_tone" value="friendly"',
            'name="response_tone" value="direct"',
            'name="response_tone" value="warm"',
            'id="settings-account-email"',
            'id="settings-logout"',
            'href="/contact/"',
            'href="#integrations"',
            'href="/conditions/"',
            "Conditions générales d’utilisation",
            'href="/confidentialite/"',
            "Politique de confidentialité",
        ):
            self.assertIn(expected, settings_markup)
        for excluded in (
            "settings-validation-status",
            "settings-gmail-status",
            "settings-sheet-status",
            "settings-telegram-status",
            'value="formal"',
            "Modifier mon mot de passe",
            "Demander la suppression de mes données",
        ):
            self.assertNotIn(excluded, settings_markup)
        self.assertIn(
            "Cette action ferme uniquement votre session SmartReply",
            settings_markup,
        )
        self.assertIn(
            'companySettings: "/api/v1/companies/me/settings"',
            js_response.text,
        )
        self.assertIn('method: "PATCH"', js_response.text)
        self.assertIn("updateSectorEditingState", js_response.text)
        self.assertIn("COMPANY_PROFILE_FIELDS", js_response.text)
        self.assertIn("body[fieldName]", js_response.text)
        self.assertIn(".settings-card", css_response.text)
        self.assertIn(".tone-option", css_response.text)
        self.assertIn(".feature-badge-new", css_response.text)
        feature_badge_styles = css_response.text.split(
            ".feature-badge-new",
            maxsplit=1,
        )[1].split(".settings-card-heading p", maxsplit=1)[0]
        self.assertIn("var(--color-success-soft)", feature_badge_styles)
        self.assertIn("var(--color-success)", feature_badge_styles)

        onboarding_markup = response.text.split(
            '<main id="company-screen"', maxsplit=1
        )[1].split('<div id="dashboard-shell"', maxsplit=1)[0]
        self.assertIn('<select id="company-sector"', onboarding_markup)
        self.assertIn('id="company-sector-other"', onboarding_markup)
        self.assertLess(
            onboarding_markup.index('id="company-name"'),
            onboarding_markup.index('id="connect-drive-onboarding"'),
        )
        self.assertIn(
            "Renseignez d'abord votre entreprise, puis autorisez Google Drive",
            onboarding_markup,
        )
        self.assertIn("Créer mon entreprise et mon Sheet", onboarding_markup)
        for sector in (
            "Conseil",
            "Services",
            "Commerce",
            "E-commerce",
            "Formation",
            "Marketing et communication",
            "Technologie",
            "Freelance",
            "Agence",
            "Immobilier",
        ):
            self.assertIn(f'value="{sector}"', onboarding_markup)

    def test_company_details_are_saved_before_drive_and_restored_after_oauth(self):
        repository_root = Path(__file__).resolve().parents[1]
        script = r"""
const fs = require("fs");
const vm = require("vm");
let source = fs.readFileSync("app/dashboard/app.js", "utf8");
const marker = '  document.addEventListener("DOMContentLoaded", initialize);\n})();';
if (!source.includes(marker)) throw new Error("Dashboard test export marker missing");
source = source.replace(
  marker,
  `  globalThis.__onboardingDraftTest = {
    persistOnboardingCompanyDraft,
    readOnboardingCompanyDraft,
    restoreOnboardingCompanyDraft,
    clearOnboardingCompanyDraft,
  };\n})();`,
);
const storage = new Map();
const elements = new Map();
const element = (id) => {
  if (!elements.has(id)) {
    elements.set(id, {
      value: "",
      hidden: false,
      disabled: false,
      required: false,
    });
  }
  return elements.get(id);
};
const context = {
  window: {
    localStorage: { getItem: () => null },
    sessionStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    },
  },
  document: { getElementById: (id) => element(id) },
  console,
  Intl,
  Set,
};
vm.createContext(context);
vm.runInContext(source, context);
const subject = context.__onboardingDraftTest;
element("company-name").value = "  Atelier Koffa  ";
element("company-sector").value = "__other__";
element("company-sector-other").value = "  Artisanat local  ";
const persisted = subject.persistOnboardingCompanyDraft();
element("company-name").value = "";
element("company-sector").value = "";
element("company-sector-other").value = "";
const restored = subject.restoreOnboardingCompanyDraft();
const result = {
  persisted,
  restored,
  draft: subject.readOnboardingCompanyDraft(),
  companyName: element("company-name").value,
  sectorSelect: element("company-sector").value,
  otherSector: element("company-sector-other").value,
  otherVisible: !element("company-sector-other-field").hidden,
};
subject.clearOnboardingCompanyDraft();
result.cleared = subject.readOnboardingCompanyDraft() === null;
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

        self.assertTrue(result["persisted"])
        self.assertTrue(result["restored"])
        self.assertTrue(result["cleared"])
        self.assertEqual(
            result["draft"],
            {
                "nom_entreprise": "Atelier Koffa",
                "secteur": "Artisanat local",
            },
        )
        self.assertEqual(result["companyName"], "Atelier Koffa")
        self.assertEqual(result["sectorSelect"], "__other__")
        self.assertEqual(result["otherSector"], "Artisanat local")
        self.assertTrue(result["otherVisible"])

        javascript = (repository_root / "app/dashboard/app.js").read_text()
        connect_drive_source = javascript.split(
            "async function connectDrive", maxsplit=1
        )[1].split("async function handleCompanyCreation", maxsplit=1)[0]
        self.assertLess(
            connect_drive_source.index("persistOnboardingCompanyDraft()"),
            connect_drive_source.index("apiRequest(API.driveConnect)"),
        )

    def test_company_validation_is_limited_to_sidebar_and_topbar(self):
        response = self.client.get("/dashboard/")
        js_response = self.client.get("/dashboard/app.js")
        css_response = self.client.get("/dashboard/styles.css")

        self.assertEqual(response.status_code, 200)
        sidebar_markup = response.text.split(
            '<aside id="sidebar"', maxsplit=1
        )[1].split("</aside>", maxsplit=1)[0]
        topbar_markup = response.text.split(
            '<header class="topbar">', maxsplit=1
        )[1].split("</header>", maxsplit=1)[0]
        dashboard_markup = response.text.split(
            '<section id="dashboard-view"', maxsplit=1
        )[1].split('<section id="emails-view"', maxsplit=1)[0]
        settings_markup = response.text.split(
            '<section id="settings-view"', maxsplit=1
        )[1].split("</main>", maxsplit=1)[0]

        self.assertIn('id="sidebar-validation-status"', sidebar_markup)
        self.assertIn('class="company-summary-copy"', sidebar_markup)
        self.assertNotIn("sidebar-technical-status", sidebar_markup)
        self.assertIn('id="topbar-validation-status"', topbar_markup)
        self.assertIn(
            'aria-label="Statut de validation de l\'entreprise"',
            topbar_markup,
        )
        for removed_central_element in (
            "company-status-panel",
            "company-status-title",
            "company-status-description",
            "dashboard-validation-status",
            "dashboard-technical-status",
            "Validation DIGICRAFT LABS",
            "Configuration technique",
        ):
            self.assertNotIn(removed_central_element, dashboard_markup)
        self.assertIn('id="dashboard-connected-count"', dashboard_markup)
        self.assertIn("intégrations connectées", dashboard_markup)
        self.assertNotIn("validation-status", settings_markup)
        self.assertNotIn("Statut de l'entreprise", settings_markup)
        self.assertIn(
            'companyValidationStatus: "/api/v1/companies/me/validation-status"',
            js_response.text,
        )
        for expected_label in ("EN ATTENTE", "VALIDÉE", "INDISPONIBLE"):
            self.assertIn(expected_label, js_response.text)
        self.assertNotIn("À COMPLÉTER", js_response.text)
        self.assertNotIn("PRÊTE", js_response.text)
        self.assertIn(".company-summary-copy .status-pill", css_response.text)
        self.assertIn(".topbar-validation-status", css_response.text)
        self.assertIn(".status-pill.warning", css_response.text)
        self.assertNotIn(".company-status-panel", css_response.text)

    def test_dashboard_email_and_integration_routes_render_the_expected_view(self):
        repository_root = Path(__file__).resolve().parents[1]
        script = r"""
const fs = require("fs");
const vm = require("vm");
let source = fs.readFileSync("app/dashboard/app.js", "utf8");
const marker = '  document.addEventListener("DOMContentLoaded", initialize);\n})();';
if (!source.includes(marker)) throw new Error("Dashboard test export marker missing");
source = source.replace(
  '      checkIntegrationStatuses({ silent: true });',
  '      void 0;',
);
source = source.replace(
  '      void loadEmailsPage(1);',
  '      state.emailList.loaded = true; globalThis.__emailsLoadCount += 1;',
);
source = source.replace(
  '      void loadDraftsPage(1);',
  '      state.draftList.loaded = true; globalThis.__draftsLoadCount += 1;',
);
source = source.replace(
  marker,
  '  globalThis.__routeTest = { currentRoute, renderRoute, state };\n})();',
);

const makeClassList = () => {
  const values = new Set();
  return {
    add: (name) => values.add(name),
    remove: (name) => values.delete(name),
    toggle: (name, active) => active ? values.add(name) : values.delete(name),
    contains: (name) => values.has(name),
  };
};
const makeElement = (dataset = {}) => {
  const attributes = {};
  return {
    hidden: false,
    textContent: "",
    dataset,
    classList: makeClassList(),
    setAttribute(name, value) { attributes[name] = value; },
    removeAttribute(name) { delete attributes[name]; },
    getAttribute(name) { return attributes[name]; },
  };
};
const elements = {
  "dashboard-view": makeElement(),
  "emails-view": makeElement(),
  "drafts-view": makeElement(),
  "integrations-view": makeElement(),
  "settings-view": makeElement(),
  "topbar-title": makeElement(),
  "sidebar": makeElement(),
  "sidebar-overlay": makeElement(),
  "open-sidebar": makeElement(),
};
const navItems = [
  "dashboard",
  "emails",
  "drafts",
  "integrations",
  "settings",
].map((route) => makeElement({ route }));
const context = {
  __emailsLoadCount: 0,
  __draftsLoadCount: 0,
  window: {
    localStorage: { getItem: () => null },
    location: { hash: "#dashboard" },
    setTimeout: () => 0,
  },
  document: {
    getElementById: (id) => {
      if (!elements[id]) elements[id] = makeElement();
      return elements[id];
    },
    querySelectorAll: (selector) => selector === "[data-route]" ? navItems : [],
  },
  console,
  Intl,
  Set,
};
vm.createContext(context);
vm.runInContext(source, context);
const subject = context.__routeTest;
subject.state.appReady = true;

const snapshot = (hash) => {
  context.window.location.hash = hash;
  subject.renderRoute();
  return {
    route: subject.currentRoute(),
    title: elements["topbar-title"].textContent,
    visible: [
      "dashboard",
      "emails",
      "drafts",
      "integrations",
      "settings",
    ].filter((route) => !elements[`${route}-view`].hidden),
    active: navItems.filter((item) => item.classList.contains("active"))
      .map((item) => item.dataset.route),
    current: navItems.filter((item) => item.getAttribute("aria-current") === "page")
      .map((item) => item.dataset.route),
  };
};
process.stdout.write(JSON.stringify({
  dashboard: snapshot("#dashboard"),
  emails: snapshot("#emails"),
  drafts: snapshot("#drafts"),
  integrations: snapshot("#integrations"),
  settings: snapshot("#settings"),
  fallback: snapshot("#unknown"),
  emailsAgain: snapshot("#emails"),
  draftsAgain: snapshot("#drafts"),
  emailsLoadCount: context.__emailsLoadCount,
  draftsLoadCount: context.__draftsLoadCount,
}));
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)

        for route, title in (
            ("dashboard", "Tableau de bord"),
            ("emails", "Emails"),
            ("drafts", "Brouillons"),
            ("integrations", "Intégrations"),
            ("settings", "Paramètres"),
        ):
            with self.subTest(route=route):
                self.assertEqual(result[route]["route"], route)
                self.assertEqual(result[route]["title"], title)
                self.assertEqual(result[route]["visible"], [route])
                self.assertEqual(result[route]["active"], [route])
                self.assertEqual(result[route]["current"], [route])
        self.assertEqual(result["fallback"]["route"], "dashboard")
        self.assertEqual(result["fallback"]["visible"], ["dashboard"])
        self.assertEqual(result["emailsAgain"]["visible"], ["emails"])
        self.assertEqual(result["draftsAgain"]["visible"], ["drafts"])
        self.assertEqual(result["emailsLoadCount"], 1)
        self.assertEqual(result["draftsLoadCount"], 1)

    def test_emails_api_loading_rendering_states_and_session_purge(self):
        repository_root = Path(__file__).resolve().parents[1]
        script = r"""
const fs = require("fs");
const vm = require("vm");
let source = fs.readFileSync("app/dashboard/app.js", "utf8");
const marker = '  document.addEventListener("DOMContentLoaded", initialize);\n})();';
if (!source.includes(marker)) throw new Error("Dashboard test export marker missing");
source = source.replace(
  marker,
  `  globalThis.__emailsApiTest = {
    applyEmailFilters,
    cacheEmailsViewElements,
    loadEmailsPage,
    renderRoute,
    retryEmailsPage,
    scheduleEmailSearch,
    state,
  };\n})();`,
);

let innerHtmlWrites = 0;
class TestElement {
  constructor(tagName = "div") {
    this.tagName = tagName;
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.className = "";
    this.dataset = {};
    this.children = [];
    this.attributes = {};
    this.open = false;
    this._textContent = "";
    this.classList = {
      add() {},
      remove() {},
      toggle() {},
    };
  }
  set textContent(value) {
    this._textContent = String(value);
  }
  get textContent() {
    return this._textContent;
  }
  set innerHTML(_value) {
    innerHtmlWrites += 1;
  }
  append(...children) {
    this.children.push(...children);
  }
  replaceChildren(...children) {
    this.children = [...children];
    this._textContent = "";
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  removeAttribute(name) {
    delete this.attributes[name];
  }
  addEventListener() {}
  focus() {}
  close() { this.open = false; }
}

const elements = new Map();
const element = (id) => {
  if (!elements.has(id)) elements.set(id, new TestElement());
  return elements.get(id);
};
let storedTokenRemoved = false;
let replacedPath = null;
let fetchHandler = null;
let nextTimerId = 1;
const timers = new Map();
const context = {
  window: {
    localStorage: {
      getItem: () => null,
      setItem() {},
      removeItem: () => { storedTokenRemoved = true; },
    },
    location: { hash: "#emails" },
    history: { replaceState: (_state, _title, path) => { replacedPath = path; } },
    fetch: (path, options) => fetchHandler(path, options),
    setTimeout: (callback, delay) => {
      const timerId = nextTimerId;
      nextTimerId += 1;
      timers.set(timerId, { callback, delay });
      return timerId;
    },
    clearTimeout: (timerId) => timers.delete(timerId),
    clearInterval() {},
  },
  document: {
    getElementById: (id) => element(id),
    createElement: (tagName) => new TestElement(tagName),
    querySelectorAll: () => [],
  },
  console,
  Intl,
  Set,
  URLSearchParams,
};
const response = (status, payload) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: { get: () => "application/json" },
  json: async () => payload,
  text: async () => JSON.stringify(payload),
});
const flushAsyncWork = () => new Promise((resolve) => setImmediate(resolve));
const runPendingTimers = async () => {
  const pending = [...timers.values()];
  timers.clear();
  pending.forEach(({ callback }) => callback());
  await flushAsyncWork();
};
vm.createContext(context);
vm.runInContext(source, context);
const subject = context.__emailsApiTest;
subject.cacheEmailsViewElements();
subject.state.token = "test-session-token";

(async () => {
  let firstRequest = null;
  fetchHandler = async (path, options) => {
    firstRequest = { path, options };
    return response(200, {
      items: [{
        email_id: "EM-41",
        sender_name: "<img src=x onerror=alert(1)>",
        sender_email: "sender@example.test",
        subject: "<script>unsafe()</script>",
        received_at: "2026-10-30T12:45:00Z",
        summary: "Résumé public",
        category: "Support",
        priority: "Haute",
        status: "Brouillon prêt",
      }],
      pagination: {
        page: 1,
        page_size: 25,
        total_items: 51,
        total_pages: 3,
      },
    });
  };
  await subject.loadEmailsPage(1);
  const firstRow = element("emails-table-body").children[0];
  const success = {
    path: firstRequest.path,
    bearerSent: (
      firstRequest.options.headers.Authorization
      === "Bearer test-session-token"
    ),
    method: firstRequest.options.method,
    senderName: firstRow.children[0].children[0].textContent,
    senderEmail: firstRow.children[0].children[1].textContent,
    subject: firstRow.children[1].children[0].textContent,
    dateRendered: firstRow.children[2].textContent !== "—",
    category: firstRow.children[3].children[0].textContent,
    priority: firstRow.children[4].children[0].textContent,
    status: firstRow.children[5].children[0].textContent,
    emailId: firstRow.dataset.emailId,
    paginationSummary: element("emails-pagination-summary").textContent,
    pageIndicator: element("emails-page-indicator").textContent,
    previousDisabled: element("emails-previous-page").disabled,
    nextDisabled: element("emails-next-page").disabled,
    tableVisible: !element("emails-table-wrap").hidden,
    paginationVisible: !element("emails-pagination").hidden,
    loadingHidden: element("emails-loading-state").hidden,
    innerHtmlWrites,
  };

  let resolveOldRequest;
  fetchHandler = (path) => {
    if (path.includes("page=1")) {
      return new Promise((resolve) => {
        resolveOldRequest = () => resolve(response(200, {
          items: [{
            email_id: "EM-1",
            sender_name: "Ancien",
            sender_email: "old@example.test",
            subject: "Réponse obsolète",
            received_at: null,
            summary: "",
            category: "Autre",
            priority: "Basse",
            status: "Analysé",
          }],
          pagination: { page: 1, page_size: 25, total_items: 50, total_pages: 2 },
        }));
      });
    }
    return Promise.resolve(response(200, {
      items: [{
        email_id: "EM-26",
        sender_name: "Récent",
        sender_email: "new@example.test",
        subject: "Réponse la plus récente",
        received_at: null,
        summary: "",
        category: "Devis",
        priority: "Moyenne",
        status: "Envoyé",
      }],
      pagination: { page: 2, page_size: 25, total_items: 50, total_pages: 2 },
    }));
  };
  const oldLoad = subject.loadEmailsPage(1);
  const loadingState = {
    visible: !element("emails-loading-state").hidden,
    tableHidden: element("emails-table-wrap").hidden,
    ariaBusy: element("emails-workspace").attributes["aria-busy"],
  };
  const latestLoad = subject.loadEmailsPage(2);
  await latestLoad;
  resolveOldRequest();
  await oldLoad;
  const staleProtection = {
    page: subject.state.emailList.pagination.page,
    subject: (
      element("emails-table-body").children[0].children[1].children[0].textContent
    ),
    previousDisabled: element("emails-previous-page").disabled,
    nextDisabled: element("emails-next-page").disabled,
  };

  const correctedCriteria = {
    search: "résultats réduits",
    category: "Support",
    priority: "Moyenne",
    status: "Analysé",
  };
  subject.state.emailList.criteria = { ...correctedCriteria };
  const correctedPageRequests = [];
  fetchHandler = async (path) => {
    const page = Number(new URLSearchParams(path.split("?", 2)[1]).get("page"));
    correctedPageRequests.push(page);
    if (page === 3) {
      return response(200, {
        items: [],
        pagination: { page: 3, page_size: 25, total_items: 30, total_pages: 2 },
      });
    }
    return response(200, {
      items: [{
        email_id: "EM-30",
        sender_name: "Page corrigée",
        sender_email: "corrected@example.test",
        subject: "Dernière page valide",
        received_at: null,
        summary: "",
        category: "Support",
        priority: "Moyenne",
        status: "Analysé",
      }],
      pagination: { page: 2, page_size: 25, total_items: 30, total_pages: 2 },
    });
  };
  await subject.loadEmailsPage(3);
  const correctedPageState = {
    requests: correctedPageRequests,
    page: subject.state.emailList.pagination.page,
    criteria: subject.state.emailList.criteria,
    subject: (
      element("emails-table-body").children[0].children[1].children[0].textContent
    ),
  };
  subject.state.emailList.criteria = {
    search: "",
    category: "",
    priority: "",
    status: "",
  };

  let trueEmptyRequestCount = 0;
  fetchHandler = async () => {
    trueEmptyRequestCount += 1;
    return response(200, {
      items: [],
      pagination: { page: 1, page_size: 25, total_items: 0, total_pages: 0 },
    });
  };
  await subject.loadEmailsPage(1);
  const emptyState = {
    visible: !element("emails-empty-state").hidden,
    tableHidden: element("emails-table-wrap").hidden,
    loaded: subject.state.emailList.loaded,
    requestCount: trueEmptyRequestCount,
  };

  const repeatedCorrectionRequests = [];
  fetchHandler = async (path) => {
    const page = Number(new URLSearchParams(path.split("?", 2)[1]).get("page"));
    repeatedCorrectionRequests.push(page);
    if (page === 3) {
      return response(200, {
        items: [],
        pagination: { page: 3, page_size: 25, total_items: 10, total_pages: 1 },
      });
    }
    return response(200, {
      items: [],
      pagination: { page: 2, page_size: 25, total_items: 10, total_pages: 1 },
    });
  };
  await subject.loadEmailsPage(3);
  const repeatedCorrectionState = {
    requests: repeatedCorrectionRequests,
    page: subject.state.emailList.pagination.page,
    loaded: subject.state.emailList.loaded,
    emptyVisible: !element("emails-empty-state").hidden,
  };

  fetchHandler = async () => response(502, { detail: "Indisponible" });
  await subject.loadEmailsPage(1);
  const errorState = {
    visible: !element("emails-error-state").hidden,
    loadingHidden: element("emails-loading-state").hidden,
    loaded: subject.state.emailList.loaded,
  };

  let resolveRetryRequest;
  const retryRequests = [];
  fetchHandler = (path) => {
    retryRequests.push(path);
    return new Promise((resolve) => {
      resolveRetryRequest = () => resolve(response(200, {
        items: [],
        pagination: { page: 1, page_size: 25, total_items: 0, total_pages: 0 },
      }));
    });
  };
  const firstRetryStarted = subject.retryEmailsPage();
  const secondRetrySkipped = !subject.retryEmailsPage();
  const retryLoadingState = {
    loading: subject.state.emailList.loading,
    buttonDisabled: element("emails-retry").disabled,
  };
  resolveRetryRequest();
  await flushAsyncWork();
  const retryState = {
    firstRetryStarted,
    secondRetrySkipped,
    requestCount: retryRequests.length,
    path: retryRequests[0],
    retryLoadingState,
    loaded: subject.state.emailList.loaded,
    emptyVisible: !element("emails-empty-state").hidden,
  };

  const criteriaRequests = [];
  fetchHandler = async (path) => {
    criteriaRequests.push(path);
    const query = new URLSearchParams(path.split("?", 2)[1]);
    const page = Number(query.get("page"));
    return response(200, {
      items: [],
      pagination: {
        page,
        page_size: 25,
        total_items: 0,
        total_pages: 0,
      },
    });
  };
  const searchInput = element("emails-search");
  const categoryFilter = element("emails-category-filter");
  const priorityFilter = element("emails-priority-filter");
  const statusFilter = element("emails-status-filter");

  searchInput.value = "Ali";
  subject.scheduleEmailSearch();
  searchInput.value = "Alice";
  subject.scheduleEmailSearch();
  searchInput.value = "Alice Dupont";
  subject.scheduleEmailSearch();
  const debounceBeforeRun = {
    requestCount: criteriaRequests.length,
    timerCount: timers.size,
    delay: [...timers.values()][0].delay,
  };
  await runPendingTimers();
  const senderSearchUrl = criteriaRequests.at(-1);

  searchInput.value = "alice@example.test";
  subject.scheduleEmailSearch();
  await runPendingTimers();
  const addressSearchUrl = criteriaRequests.at(-1);

  searchInput.value = "Objet urgent";
  subject.scheduleEmailSearch();
  await runPendingTimers();
  const subjectSearchUrl = criteriaRequests.at(-1);

  searchInput.value = "   ";
  subject.scheduleEmailSearch();
  await runPendingTimers();
  const clearedSearchUrl = criteriaRequests.at(-1);
  const requestCountAfterClear = criteriaRequests.length;
  searchInput.value = "";
  subject.scheduleEmailSearch();
  const repeatedEmptySearch = {
    timerCount: timers.size,
    requestCountUnchanged: criteriaRequests.length === requestCountAfterClear,
  };

  categoryFilter.value = "Devis";
  subject.applyEmailFilters();
  await flushAsyncWork();
  const categoryUrl = criteriaRequests.at(-1);

  categoryFilter.value = "";
  priorityFilter.value = "Haute";
  subject.applyEmailFilters();
  await flushAsyncWork();
  const priorityUrl = criteriaRequests.at(-1);

  priorityFilter.value = "";
  statusFilter.value = "Analysé";
  subject.applyEmailFilters();
  await flushAsyncWork();
  const statusUrl = criteriaRequests.at(-1);

  subject.state.emailList.pagination.page = 3;
  searchInput.value = "client@example.test";
  categoryFilter.value = "Support";
  priorityFilter.value = "Moyenne";
  statusFilter.value = "Brouillon prêt";
  subject.scheduleEmailSearch();
  const requestCountBeforeFilterChange = criteriaRequests.length;
  subject.applyEmailFilters();
  await flushAsyncWork();
  const filterChangeAfterPendingSearch = {
    timerCount: timers.size,
    addedRequests: criteriaRequests.length - requestCountBeforeFilterChange,
  };
  const combinedUrl = criteriaRequests.at(-1);
  const pageAfterCriteriaChange = subject.state.emailList.pagination.page;

  await subject.loadEmailsPage(2);
  const secondPageUrl = criteriaRequests.at(-1);
  const requestCountBeforeDuplicate = criteriaRequests.length;
  subject.applyEmailFilters();
  await flushAsyncWork();
  const duplicateCriteriaSkipped = (
    criteriaRequests.length === requestCountBeforeDuplicate
  );

  searchInput.value = "recherche qui ne doit pas partir";
  subject.scheduleEmailSearch();
  const requestCountBeforeNavigation = criteriaRequests.length;
  subject.state.appReady = true;
  context.window.location.hash = "#dashboard";
  subject.renderRoute();
  const pendingSearchTimerCanceled = ![...timers.values()].some(
    ({ delay }) => delay === 350,
  );
  await runPendingTimers();
  const navigationDebounce = {
    pendingSearchTimerCanceled,
    requestCountUnchanged: (
      criteriaRequests.length === requestCountBeforeNavigation
    ),
    activeSearch: subject.state.emailList.criteria.search,
    controlSearch: searchInput.value,
  };

  searchInput.value = "x".repeat(205);
  subject.scheduleEmailSearch();
  await runPendingTimers();
  const clippedSearchLength = new URLSearchParams(
    criteriaRequests.at(-1).split("?", 2)[1],
  ).get("search").length;

  fetchHandler = async () => response(503, { detail: "Indisponible" });
  await subject.loadEmailsPage(1);
  let retryWithCriteriaUrl = null;
  fetchHandler = async (path) => {
    retryWithCriteriaUrl = path;
    return response(200, {
      items: [],
      pagination: { page: 1, page_size: 25, total_items: 0, total_pages: 0 },
    });
  };
  subject.retryEmailsPage();
  await flushAsyncWork();
  const retryWithCriteriaQuery = new URLSearchParams(
    retryWithCriteriaUrl.split("?", 2)[1],
  );
  const retryWithCriteria = {
    page: retryWithCriteriaQuery.get("page"),
    pageSize: retryWithCriteriaQuery.get("page_size"),
    searchLength: retryWithCriteriaQuery.get("search").length,
    category: retryWithCriteriaQuery.get("category"),
    priority: retryWithCriteriaQuery.get("priority"),
    status: retryWithCriteriaQuery.get("status"),
  };

  const criteriaBehavior = {
    debounceBeforeRun,
    senderSearchUrl,
    addressSearchUrl,
    subjectSearchUrl,
    clearedSearchUrl,
    repeatedEmptySearch,
    categoryUrl,
    priorityUrl,
    statusUrl,
    filterChangeAfterPendingSearch,
    combinedUrl,
    pageAfterCriteriaChange,
    secondPageUrl,
    duplicateCriteriaSkipped,
    navigationDebounce,
    clippedSearchLength,
    retryWithCriteria,
  };

  fetchHandler = async () => response(200, {
    items: [{
      email_id: "EM-99",
      sender_name: "À purger",
      sender_email: "purge@example.test",
      subject: "Donnée de session",
      received_at: null,
      summary: "",
      category: "Autre",
      priority: "Basse",
      status: "Analysé",
    }],
    pagination: { page: 1, page_size: 25, total_items: 1, total_pages: 1 },
  });
  await subject.loadEmailsPage(1);
  fetchHandler = async () => response(401, { detail: "Session expirée" });
  await subject.loadEmailsPage(1);
  const unauthorized = {
    tokenCleared: subject.state.token === null,
    itemsPurged: subject.state.emailList.items.length === 0,
    rowsPurged: element("emails-table-body").children.length === 0,
    storedTokenRemoved,
    authScreenVisible: !element("auth-screen").hidden,
    redirectedTo: replacedPath,
    errorHidden: element("emails-error-state").hidden,
  };

  process.stdout.write(JSON.stringify({
    success,
    loadingState,
    staleProtection,
    correctedPageState,
    emptyState,
    repeatedCorrectionState,
    errorState,
    retryState,
    criteriaBehavior,
    unauthorized,
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

        self.assertEqual(
            result["success"]["path"],
            "/api/v1/dashboard/emails?page=1&page_size=25",
        )
        self.assertTrue(result["success"]["bearerSent"])
        self.assertEqual(result["success"]["method"], "GET")
        self.assertEqual(
            result["success"]["senderName"],
            "<img src=x onerror=alert(1)>",
        )
        self.assertEqual(result["success"]["senderEmail"], "sender@example.test")
        self.assertEqual(
            result["success"]["subject"],
            "<script>unsafe()</script>",
        )
        self.assertTrue(result["success"]["dateRendered"])
        self.assertEqual(result["success"]["category"], "Support")
        self.assertEqual(result["success"]["priority"], "Haute")
        self.assertEqual(result["success"]["status"], "Brouillon prêt")
        self.assertEqual(result["success"]["emailId"], "EM-41")
        self.assertEqual(
            result["success"]["paginationSummary"],
            "1–1 sur 51 emails",
        )
        self.assertEqual(result["success"]["pageIndicator"], "Page 1 sur 3")
        self.assertTrue(result["success"]["previousDisabled"])
        self.assertFalse(result["success"]["nextDisabled"])
        self.assertTrue(result["success"]["tableVisible"])
        self.assertTrue(result["success"]["paginationVisible"])
        self.assertTrue(result["success"]["loadingHidden"])
        self.assertEqual(result["success"]["innerHtmlWrites"], 0)
        self.assertEqual(
            result["loadingState"],
            {"visible": True, "tableHidden": True, "ariaBusy": "true"},
        )

        self.assertEqual(result["staleProtection"]["page"], 2)
        self.assertEqual(
            result["staleProtection"]["subject"],
            "Réponse la plus récente",
        )
        self.assertFalse(result["staleProtection"]["previousDisabled"])
        self.assertTrue(result["staleProtection"]["nextDisabled"])

        self.assertEqual(
            result["correctedPageState"],
            {
                "requests": [3, 2],
                "page": 2,
                "criteria": {
                    "search": "résultats réduits",
                    "category": "Support",
                    "priority": "Moyenne",
                    "status": "Analysé",
                },
                "subject": "Dernière page valide",
            },
        )
        self.assertEqual(
            result["emptyState"],
            {
                "visible": True,
                "tableHidden": True,
                "loaded": True,
                "requestCount": 1,
            },
        )
        self.assertEqual(
            result["repeatedCorrectionState"],
            {
                "requests": [3, 1],
                "page": 2,
                "loaded": True,
                "emptyVisible": True,
            },
        )
        self.assertEqual(
            result["errorState"],
            {"visible": True, "loadingHidden": True, "loaded": False},
        )
        self.assertEqual(
            result["retryState"],
            {
                "firstRetryStarted": True,
                "secondRetrySkipped": True,
                "requestCount": 1,
                "path": "/api/v1/dashboard/emails?page=1&page_size=25",
                "retryLoadingState": {
                    "loading": True,
                    "buttonDisabled": True,
                },
                "loaded": True,
                "emptyVisible": True,
            },
        )

        criteria = result["criteriaBehavior"]
        self.assertEqual(
            criteria["debounceBeforeRun"],
            {"requestCount": 0, "timerCount": 1, "delay": 350},
        )
        self.assertEqual(
            criteria["senderSearchUrl"],
            "/api/v1/dashboard/emails?page=1&page_size=25&search=Alice+Dupont",
        )
        self.assertEqual(
            criteria["addressSearchUrl"],
            (
                "/api/v1/dashboard/emails?page=1&page_size=25"
                "&search=alice%40example.test"
            ),
        )
        self.assertEqual(
            criteria["subjectSearchUrl"],
            "/api/v1/dashboard/emails?page=1&page_size=25&search=Objet+urgent",
        )
        self.assertEqual(
            criteria["clearedSearchUrl"],
            "/api/v1/dashboard/emails?page=1&page_size=25",
        )
        self.assertEqual(
            criteria["repeatedEmptySearch"],
            {"timerCount": 0, "requestCountUnchanged": True},
        )
        self.assertEqual(
            criteria["categoryUrl"],
            "/api/v1/dashboard/emails?page=1&page_size=25&category=Devis",
        )
        self.assertEqual(
            criteria["priorityUrl"],
            "/api/v1/dashboard/emails?page=1&page_size=25&priority=Haute",
        )
        self.assertEqual(
            criteria["statusUrl"],
            (
                "/api/v1/dashboard/emails?page=1&page_size=25"
                "&status=Analys%C3%A9"
            ),
        )
        self.assertEqual(
            criteria["filterChangeAfterPendingSearch"],
            {"timerCount": 0, "addedRequests": 1},
        )
        self.assertEqual(
            criteria["combinedUrl"],
            (
                "/api/v1/dashboard/emails?page=1&page_size=25"
                "&search=client%40example.test&category=Support"
                "&priority=Moyenne&status=Brouillon+pr%C3%AAt"
            ),
        )
        self.assertEqual(criteria["pageAfterCriteriaChange"], 1)
        self.assertEqual(
            criteria["secondPageUrl"],
            (
                "/api/v1/dashboard/emails?page=2&page_size=25"
                "&search=client%40example.test&category=Support"
                "&priority=Moyenne&status=Brouillon+pr%C3%AAt"
            ),
        )
        self.assertTrue(criteria["duplicateCriteriaSkipped"])
        self.assertEqual(
            criteria["navigationDebounce"],
            {
                "pendingSearchTimerCanceled": True,
                "requestCountUnchanged": True,
                "activeSearch": "client@example.test",
                "controlSearch": "client@example.test",
            },
        )
        self.assertEqual(criteria["clippedSearchLength"], 200)
        self.assertEqual(
            criteria["retryWithCriteria"],
            {
                "page": "1",
                "pageSize": "25",
                "searchLength": 200,
                "category": "Support",
                "priority": "Moyenne",
                "status": "Brouillon prêt",
            },
        )

        self.assertEqual(
            result["unauthorized"],
            {
                "tokenCleared": True,
                "itemsPurged": True,
                "rowsPurged": True,
                "storedTokenRemoved": True,
                "authScreenVisible": True,
                "redirectedTo": "/dashboard/",
                "errorHidden": True,
            },
        )

    def test_email_detail_navigation_states_security_and_session_purge(self):
        repository_root = Path(__file__).resolve().parents[1]
        script = r"""
const fs = require("fs");
const vm = require("vm");
let source = fs.readFileSync("app/dashboard/app.js", "utf8");
const marker = '  document.addEventListener("DOMContentLoaded", initialize);\n})();';
if (!source.includes(marker)) throw new Error("Dashboard test export marker missing");
source = source.replace(
  marker,
  `  globalThis.__emailDetailTest = {
    cacheEmailsViewElements,
    loadDashboardData,
    loadEmailDetail,
    loadEmailsPage,
    renderRoute,
    returnToEmailList,
    state,
  };\n})();`,
);

let innerHtmlWrites = 0;
class TestElement {
  constructor(tagName = "div") {
    this.tagName = tagName;
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.className = "";
    this.dataset = {};
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.open = false;
    this.tabIndex = -1;
    this._textContent = "";
    this.classList = {
      add: (...names) => {
        const values = new Set(this.className.split(" ").filter(Boolean));
        names.forEach((name) => values.add(name));
        this.className = [...values].join(" ");
      },
      remove: (...names) => {
        const removed = new Set(names);
        this.className = this.className.split(" ")
          .filter((name) => name && !removed.has(name)).join(" ");
      },
      toggle: (name, active) => {
        if (active) this.classList.add(name);
        else this.classList.remove(name);
      },
    };
  }
  set textContent(value) {
    this._textContent = String(value);
  }
  get textContent() {
    return this._textContent;
  }
  set innerHTML(_value) {
    innerHtmlWrites += 1;
  }
  append(...children) {
    this.children.push(...children);
  }
  replaceChildren(...children) {
    this.children = [...children];
    this._textContent = "";
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  removeAttribute(name) {
    delete this.attributes[name];
  }
  addEventListener(type, listener) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(listener);
  }
  dispatchEvent(event) {
    (this.listeners[event.type] || []).forEach((listener) => listener(event));
  }
  focus() {}
  close() { this.open = false; }
}

const elements = new Map();
const element = (id) => {
  if (!elements.has(id)) elements.set(id, new TestElement());
  return elements.get(id);
};
let storedTokenRemoved = false;
let replacedPath = null;
let fetchHandler = null;
const requests = [];
const context = {
  window: {
    localStorage: {
      getItem: () => null,
      setItem() {},
      removeItem: () => { storedTokenRemoved = true; },
    },
    location: { hash: "#emails" },
    history: { replaceState: (_state, _title, path) => { replacedPath = path; } },
    fetch: (path, options) => {
      requests.push({ path, options });
      return fetchHandler(path, options);
    },
    setTimeout: (callback) => { callback(); return 1; },
    clearTimeout() {},
    clearInterval() {},
  },
  document: {
    getElementById: (id) => element(id),
    createElement: (tagName) => new TestElement(tagName),
    querySelectorAll: () => [],
  },
  console,
  Intl,
  Set,
  URLSearchParams,
};
const response = (status, payload) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: { get: () => "application/json" },
  json: async () => payload,
  text: async () => JSON.stringify(payload),
});
const flushAsyncWork = () => new Promise((resolve) => setImmediate(resolve));
vm.createContext(context);
vm.runInContext(source, context);
const subject = context.__emailDetailTest;
subject.cacheEmailsViewElements();
subject.state.token = "detail-session-token";
subject.state.appReady = true;

(async () => {
  const preservedCriteria = {
    search: "client@example.test",
    category: "Support",
    priority: "Moyenne",
    status: "Brouillon prêt",
  };
  subject.state.emailList.criteria = { ...preservedCriteria };
  element("emails-search").value = preservedCriteria.search;
  element("emails-category-filter").value = preservedCriteria.category;
  element("emails-priority-filter").value = preservedCriteria.priority;
  element("emails-status-filter").value = preservedCriteria.status;

  fetchHandler = async () => response(200, {
    items: [{
      email_id: "EM-41",
      sender_name: "Cliente",
      sender_email: "cliente@example.test",
      subject: "Demande détaillée",
      received_at: "2026-10-30T12:45:00Z",
      summary: "Résumé de liste",
      category: "Support",
      priority: "Moyenne",
      status: "Brouillon prêt",
    }],
    pagination: { page: 2, page_size: 25, total_items: 50, total_pages: 2 },
  });
  await subject.loadEmailsPage(2);
  const row = element("emails-table-body").children[0];
  const listRequestCount = requests.length;

  const detailControl = row.children[1].children[0];
  let detailControlStoppedPropagation = false;
  const accessibility = {
    rowKeepsNativeRole: !Object.hasOwn(row.attributes, "role"),
    rowNotInTabOrder: row.tabIndex === -1,
    controlTag: detailControl.tagName,
    controlLabel: detailControl.attributes["aria-label"],
  };

  fetchHandler = async () => response(200, {
    email_id: "EM-41",
    sender_name: "<img src=x onerror=alert(1)>",
    sender_email: "cliente@example.test",
    subject: "<script>Objet sûr</script>",
    received_at: "2026-10-30T12:45:00Z",
    summary: "<svg onload=alert(1)>Résumé sûr</svg>",
    category: "Support",
    priority: "Moyenne",
    status: "Brouillon prêt",
    company_id: 999,
    gmail_message_id: "internal-message-id",
    original_body: "secret original body",
  });
  detailControl.dispatchEvent({
    type: "click",
    stopPropagation: () => { detailControlStoppedPropagation = true; },
  });
  accessibility.controlStopsPropagation = detailControlStoppedPropagation;
  const hashAfterRowClick = context.window.location.hash;
  subject.renderRoute();
  await flushAsyncWork();
  const detailRequest = requests.at(-1);
  const detailSuccess = {
    hashAfterRowClick,
    path: detailRequest.path,
    method: detailRequest.options.method,
    bearerSent: (
      detailRequest.options.headers.Authorization
      === "Bearer detail-session-token"
    ),
    listHidden: element("emails-workspace").hidden,
    detailVisible: !element("email-detail-workspace").hidden,
    contentVisible: !element("email-detail-content").hidden,
    senderName: element("email-detail-sender-name").textContent,
    senderEmail: element("email-detail-sender-email").textContent,
    subject: element("email-detail-subject").textContent,
    receivedAt: element("email-detail-received-at").textContent,
    summary: element("email-detail-summary").textContent,
    category: element("email-detail-category").textContent,
    priority: element("email-detail-priority").textContent,
    status: element("email-detail-status").textContent,
    innerHtmlWrites,
    retainedKeys: Object.keys(subject.state.emailDetail.item).sort(),
  };

  const requestCountBeforeReturn = requests.length;
  subject.returnToEmailList();
  subject.renderRoute();
  const returnState = {
    hash: context.window.location.hash,
    listVisible: !element("emails-workspace").hidden,
    detailHidden: element("email-detail-workspace").hidden,
    criteria: subject.state.emailList.criteria,
    page: subject.state.emailList.pagination.page,
    controls: {
      search: element("emails-search").value,
      category: element("emails-category-filter").value,
      priority: element("emails-priority-filter").value,
      status: element("emails-status-filter").value,
    },
    noListReload: requests.length === requestCountBeforeReturn,
  };

  row.dispatchEvent({ type: "click" });
  subject.renderRoute();
  await flushAsyncWork();
  const cachedDetailSkippedRequest = requests.length === requestCountBeforeReturn;

  fetchHandler = async () => response(404, { detail: "Introuvable" });
  await subject.loadEmailDetail("EM-404");
  const notFoundState = {
    visible: !element("email-detail-not-found-state").hidden,
    contentHidden: element("email-detail-content").hidden,
    error: subject.state.emailDetail.error,
  };

  fetchHandler = async () => response(503, { detail: "Indisponible" });
  await subject.loadEmailDetail("EM-503");
  const serverErrorState = {
    visible: !element("email-detail-error-state").hidden,
    loadingHidden: element("email-detail-loading-state").hidden,
    error: subject.state.emailDetail.error,
  };

  const requestCountBeforeInvalidRoute = requests.length;
  context.window.location.hash = "#emails/not-valid";
  subject.renderRoute();
  await flushAsyncWork();
  const invalidRoute = {
    visible: !element("email-detail-not-found-state").hidden,
    noRequest: requests.length === requestCountBeforeInvalidRoute,
  };

  let resolveOldDetail;
  fetchHandler = (path) => {
    if (path.endsWith("/EM-1")) {
      return new Promise((resolve) => {
        resolveOldDetail = () => resolve(response(200, {
          sender_name: "Ancien expéditeur",
          sender_email: "old@example.test",
          subject: "Détail obsolète",
          received_at: null,
          summary: "Ancien résumé",
          category: "Autre",
          priority: "Basse",
          status: "Analysé",
        }));
      });
    }
    return Promise.resolve(response(200, {
      sender_name: "Nouvel expéditeur",
      sender_email: "new@example.test",
      subject: "Détail le plus récent",
      received_at: null,
      summary: "Nouveau résumé",
      category: "Devis",
      priority: "Haute",
      status: "Envoyé",
    }));
  };
  context.window.location.hash = "#emails/EM-1";
  subject.renderRoute();
  context.window.location.hash = "#emails/EM-2";
  subject.renderRoute();
  await flushAsyncWork();
  resolveOldDetail();
  await flushAsyncWork();
  const staleProtection = {
    emailId: subject.state.emailDetail.emailId,
    subject: element("email-detail-subject").textContent,
    summary: element("email-detail-summary").textContent,
  };

  element("greeting-name").textContent = "Personne privée";
  element("sidebar-user-email").textContent = "private@example.test";
  element("sidebar-company-name").textContent = "Entreprise privée";
  element("sheet-company-label").textContent = "Sheet : Entreprise privée";
  element("open-sheet").setAttribute("href", "https://docs.google.test/private");
  element("metric-analyzed").textContent = "42";
  element("volume-chart").append(new TestElement("private-chart"));
  element("category-chart").append(new TestElement("private-chart"));
  element("priority-chart").append(new TestElement("private-chart"));
  element("recent-emails-body").append(new TestElement("private-email"));
  element("recent-activity-list").append(new TestElement("private-activity"));
  element("telegram-code").textContent = "SR-12345";
  element("telegram-command").textContent = "/start SR-12345";
  element("telegram-code-panel").hidden = false;

  let resolveOverviewAfterLogout;
  let resolveRecentEmailsAfterLogout;
  fetchHandler = (path) => new Promise((resolve) => {
    if (path.endsWith("/overview")) {
      resolveOverviewAfterLogout = () => resolve(response(200, { metrics: {} }));
    } else {
      resolveRecentEmailsAfterLogout = () => resolve(response(200, { emails: [] }));
    }
  });
  const pendingDashboardLoad = subject.loadDashboardData({ showLoading: false });

  fetchHandler = async () => response(401, { detail: "Session expirée" });
  await subject.loadEmailDetail("EM-99");
  resolveOverviewAfterLogout();
  resolveRecentEmailsAfterLogout();
  await pendingDashboardLoad;
  const unauthorized = {
    tokenCleared: subject.state.token === null,
    listPurged: subject.state.emailList.items.length === 0,
    detailPurged: subject.state.emailDetail.item === null,
    rowsPurged: element("emails-table-body").children.length === 0,
    storedTokenRemoved,
    authScreenVisible: !element("auth-screen").hidden,
    redirectedTo: replacedPath,
    detailErrorHidden: element("email-detail-error-state").hidden,
    identityPurged: (
      element("greeting-name").textContent === "Utilisateur"
      && element("sidebar-user-email").textContent === "—"
      && element("sidebar-company-name").textContent === "—"
    ),
    dashboardPurged: (
      element("metric-analyzed").textContent === "—"
      && element("volume-chart").children.length === 0
      && element("category-chart").children.length === 0
      && element("priority-chart").children.length === 0
      && element("recent-emails-body").children.length === 0
      && element("recent-activity-list").children.length === 0
    ),
    integrationsPurged: (
      element("sheet-company-label").textContent === "Sheet de votre entreprise"
      && element("open-sheet").attributes.href === "#"
      && element("telegram-code").textContent === "SR-XXXXX"
      && element("telegram-command").textContent === "/start SR-XXXXX"
      && element("telegram-code-panel").hidden
    ),
  };

  process.stdout.write(JSON.stringify({
    listRequestCount,
    accessibility,
    detailSuccess,
    returnState,
    cachedDetailSkippedRequest,
    notFoundState,
    serverErrorState,
    invalidRoute,
    staleProtection,
    unauthorized,
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

        self.assertEqual(result["listRequestCount"], 1)
        self.assertEqual(
            result["accessibility"],
            {
                "rowKeepsNativeRole": True,
                "rowNotInTabOrder": True,
                "controlTag": "button",
                "controlLabel": (
                    "Ouvrir le détail de l'email : Demande détaillée"
                ),
                "controlStopsPropagation": True,
            },
        )
        detail = result["detailSuccess"]
        self.assertEqual(detail["hashAfterRowClick"], "#emails/EM-41")
        self.assertEqual(detail["path"], "/api/v1/dashboard/emails/EM-41")
        self.assertEqual(detail["method"], "GET")
        self.assertTrue(detail["bearerSent"])
        self.assertTrue(detail["listHidden"])
        self.assertTrue(detail["detailVisible"])
        self.assertTrue(detail["contentVisible"])
        self.assertEqual(detail["senderName"], "<img src=x onerror=alert(1)>")
        self.assertEqual(detail["senderEmail"], "cliente@example.test")
        self.assertEqual(detail["subject"], "<script>Objet sûr</script>")
        self.assertNotEqual(detail["receivedAt"], "—")
        self.assertEqual(
            detail["summary"],
            "<svg onload=alert(1)>Résumé sûr</svg>",
        )
        self.assertEqual(detail["category"], "Support")
        self.assertEqual(detail["priority"], "Moyenne")
        self.assertEqual(detail["status"], "Brouillon prêt")
        self.assertEqual(detail["innerHtmlWrites"], 0)
        self.assertEqual(
            detail["retainedKeys"],
            [
                "category",
                "priority",
                "received_at",
                "sender_email",
                "sender_name",
                "status",
                "subject",
                "summary",
            ],
        )

        self.assertEqual(
            result["returnState"],
            {
                "hash": "#emails",
                "listVisible": True,
                "detailHidden": True,
                "criteria": {
                    "search": "client@example.test",
                    "category": "Support",
                    "priority": "Moyenne",
                    "status": "Brouillon prêt",
                },
                "page": 2,
                "controls": {
                    "search": "client@example.test",
                    "category": "Support",
                    "priority": "Moyenne",
                    "status": "Brouillon prêt",
                },
                "noListReload": True,
            },
        )
        self.assertTrue(result["cachedDetailSkippedRequest"])
        self.assertEqual(
            result["notFoundState"],
            {"visible": True, "contentHidden": True, "error": "not-found"},
        )
        self.assertEqual(
            result["serverErrorState"],
            {"visible": True, "loadingHidden": True, "error": "error"},
        )
        self.assertEqual(
            result["invalidRoute"],
            {"visible": True, "noRequest": True},
        )
        self.assertEqual(
            result["staleProtection"],
            {
                "emailId": "EM-2",
                "subject": "Détail le plus récent",
                "summary": "Nouveau résumé",
            },
        )
        self.assertEqual(
            result["unauthorized"],
            {
                "tokenCleared": True,
                "listPurged": True,
                "detailPurged": True,
                "rowsPurged": True,
                "storedTokenRemoved": True,
                "authScreenVisible": True,
                "redirectedTo": "/dashboard/",
                "detailErrorHidden": True,
                "identityPurged": True,
                "dashboardPurged": True,
                "integrationsPurged": True,
            },
        )

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
            "Configurez les services requis",
            "Gmail",
            "Google Sheets",
            "Telegram pourra être ajouté ensuite, de façon optionnelle.",
            "Configurer les services requis",
        ):
            self.assertIn(expected_copy, dialog_markup)
        required_services = dialog_markup.split(
            '<ul class="integration-setup-services"', maxsplit=1
        )[1].split("</ul>", maxsplit=1)[0]
        self.assertNotIn("telegram-logo", required_services)
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
        self.assertIn(".optional-badge", css_response.text)
        self.assertIn(".emails-toolbar", css_response.text)
        self.assertIn(".emails-pagination", css_response.text)
        self.assertIn(".email-detail-workspace", css_response.text)
        self.assertIn(".email-detail-grid", css_response.text)
        self.assertIn(".email-detail-link:focus-visible", css_response.text)
        self.assertEqual(js_response.status_code, 200)
        self.assertIn("javascript", js_response.headers["content-type"])
        self.assertIn('"smartreply_access_token"', js_response.text)
        self.assertIn('"/api/v1/gmail/connect"', js_response.text)
        self.assertIn('"/api/v1/drive/connect"', js_response.text)
        self.assertIn('"/api/v1/drive/status"', js_response.text)
        self.assertIn('"/api/v1/drive/connection"', js_response.text)
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
        self.assertIn(
            "encodeURIComponent(state.company.sheet_id)",
            js_response.text,
        )
        self.assertIn("disconnectDrive", js_response.text)
        self.assertIn("repairSheetAccess", js_response.text)
        self.assertIn('$("telegram-status")', js_response.text)
        self.assertIn(
            '$("dashboard-connected-count").textContent = display.countLabel',
            js_response.text,
        )
        self.assertIn(
            'countLabel: statusesAvailable ? `${count}/3` : "—/3"',
            js_response.text,
        )
        self.assertIn(
            "Statut des intégrations temporairement indisponible",
            js_response.text,
        )
        self.assertIn("Telegram reste optionnel", js_response.text)
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
  requiredConfiguredWithoutTelegram: evaluate(
    { gmail: true, sheet: true, telegram: false },
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
            ["gmail", "sheet"],
        )
        self.assertTrue(result["oneMissing"]["shouldShow"])
        self.assertEqual(result["oneMissing"]["missing"], ["sheet"])
        self.assertTrue(result["multipleMissing"]["shouldShow"])
        self.assertEqual(result["multipleMissing"]["missing"], ["gmail"])
        self.assertFalse(result["allConfigured"]["shouldShow"])
        self.assertEqual(result["allConfigured"]["missing"], [])
        self.assertFalse(
            result["requiredConfiguredWithoutTelegram"]["shouldShow"]
        )
        self.assertEqual(
            result["requiredConfiguredWithoutTelegram"]["missing"],
            [],
        )
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
                "missing": ["gmail"],
                "closed": True,
                "dismissed": True,
                "hash": "integrations",
                "highlighted": {
                    "gmail": True,
                    "sheet": False,
                    "telegram": False,
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
            "function applyIntegrationStatusResults", maxsplit=1
        )[1].split("function formatCount", maxsplit=1)[0]
        self.assertIn("apiRequest(API.gmailStatus)", status_loader)
        self.assertIn("apiRequest(API.driveStatus)", status_loader)
        self.assertIn("apiRequest(API.telegramStatus)", status_loader)
        self.assertIn("apiRequest(API.companyValidationStatus)", status_loader)
        self.assertIn("applyCompanyValidationResult(validationResult)", status_loader)
        self.assertIn(
            (
                'const statusesAvailable = gmailResult.status === "fulfilled" '
                "&& driveAvailable"
            ),
            status_loader,
        )
        self.assertIn('telegramResult.status === "fulfilled"', status_loader)
        self.assertIn("if (!statusesAvailable) return null", status_loader)
        self.assertIn("return Object.freeze", status_loader)
        self.assertIn(
            'driveResult.value.sheet_state === "ready"',
            status_loader,
        )
        self.assertIn(
            "driveResult.value.pipeline_access === true",
            status_loader,
        )
        self.assertNotIn(
            "Boolean(state.company && state.company.sheet_id)",
            status_loader,
        )
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

    def test_optional_telegram_status_does_not_block_required_readiness(self):
        repository_root = Path(__file__).resolve().parents[1]
        script = r"""
const fs = require("fs");
const vm = require("vm");
let source = fs.readFileSync("app/dashboard/app.js", "utf8");
const marker = '  document.addEventListener("DOMContentLoaded", initialize);\n})();';
if (!source.includes(marker)) throw new Error("Dashboard test export marker missing");
source = source.replace(
  marker,
  `  globalThis.__integrationReadinessTest = {
    applyIntegrationStatusResults,
    getIntegrationDisplayState,
    state,
  };\n})();`,
);
const context = {
  window: { localStorage: { getItem: () => null } },
  console,
  Intl,
  Set,
};
vm.createContext(context);
vm.runInContext(source, context);
const subject = context.__integrationReadinessTest;
const reset = () => {
  subject.state.integrations = {
    gmail: false,
    sheet: true,
    telegram: false,
    gmailError: false,
    sheetError: false,
    sheetState: "not_created",
    sheetOwnership: "client",
    driveConnected: true,
    telegramError: false,
    telegramAvailable: null,
    statusesAvailable: false,
  };
};
const fulfilled = (value) => ({ status: "fulfilled", value });
const rejected = (status) => ({ status: "rejected", reason: { status } });
const driveReady = fulfilled({
  connected: true,
  ownership: "client",
  sheet_state: "ready",
  pipeline_access: true,
});

reset();
subject.applyIntegrationStatusResults(
  fulfilled({ connected: true }),
  rejected(503),
  driveReady,
);
const telegramOutage = {
  integrations: subject.state.integrations,
  display: subject.getIntegrationDisplayState(subject.state.integrations),
};

reset();
subject.applyIntegrationStatusResults(
  fulfilled({ connected: true }),
  fulfilled({ connected: false, available: false }),
  driveReady,
);
const telegramDisabled = {
  integrations: subject.state.integrations,
  display: subject.getIntegrationDisplayState(subject.state.integrations),
};

reset();
subject.applyIntegrationStatusResults(
  fulfilled({ connected: true }),
  fulfilled({ connected: true, available: true }),
  driveReady,
);
const allConnected = subject.getIntegrationDisplayState(
  subject.state.integrations,
);

reset();
subject.applyIntegrationStatusResults(
  fulfilled({ connected: true }),
  fulfilled({ connected: true, available: true }),
  fulfilled({
    connected: true,
    ownership: "client",
    sheet_state: "access_revoked",
    pipeline_access: false,
  }),
);
const requiredServiceMissing = subject.getIntegrationDisplayState(
  subject.state.integrations,
);

process.stdout.write(JSON.stringify({
  telegramOutage,
  telegramDisabled,
  allConnected,
  requiredServiceMissing,
}));
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)

        for case in ("telegramOutage", "telegramDisabled"):
            self.assertTrue(result[case]["integrations"]["statusesAvailable"])
            self.assertTrue(result[case]["display"]["ready"])
            self.assertEqual(result[case]["display"]["countLabel"], "2/3")
            self.assertEqual(result[case]["display"]["missingRequired"], [])
        self.assertTrue(result["telegramOutage"]["integrations"]["telegramError"])
        self.assertFalse(
            result["telegramDisabled"]["integrations"]["telegramAvailable"]
        )
        self.assertTrue(result["allConnected"]["ready"])
        self.assertEqual(result["allConnected"]["countLabel"], "3/3")
        self.assertFalse(result["requiredServiceMissing"]["ready"])
        self.assertEqual(
            result["requiredServiceMissing"]["missingRequired"],
            ["sheet"],
        )

    def test_company_status_display_uses_only_the_allowlist_result(self):
        repository_root = Path(__file__).resolve().parents[1]
        script = r"""
const fs = require("fs");
const vm = require("vm");
let source = fs.readFileSync("app/dashboard/app.js", "utf8");
const marker = '  document.addEventListener("DOMContentLoaded", initialize);\n})();';
if (!source.includes(marker)) throw new Error("Dashboard test export marker missing");
source = source.replace(
  marker,
  `  globalThis.__companyStatusTest = {
    applyCompanyValidationResult,
    getCompanyStatusDisplay,
    state,
  };\n})();`,
);
const context = {
  window: { localStorage: { getItem: () => null } },
  console,
  Intl,
  Set,
};
vm.createContext(context);
vm.runInContext(source, context);
const subject = context.__companyStatusTest;
const fulfilled = (validation_status) => ({
  status: "fulfilled",
  value: { validation_status },
});
const rejected = (status) => ({ status: "rejected", reason: { status } });
const displayAfter = (result) => {
  subject.state.companyValidation = { status: null, error: false };
  subject.applyCompanyValidationResult(result);
  return subject.getCompanyStatusDisplay();
};
const result = {
  pending: displayAfter(fulfilled("pending")),
  validated: displayAfter(fulfilled("validated")),
  unavailable: displayAfter(rejected(503)),
  invalidPayload: displayAfter(fulfilled("unexpected")),
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

        self.assertEqual(
            result["pending"],
            {"label": "EN ATTENTE", "tone": "warning"},
        )
        self.assertEqual(
            result["validated"],
            {"label": "VALIDÉE", "tone": "connected"},
        )
        self.assertEqual(
            result["unavailable"],
            {"label": "INDISPONIBLE", "tone": "error"},
        )
        self.assertEqual(
            result["invalidPayload"],
            {"label": "INDISPONIBLE", "tone": "error"},
        )

    def test_integration_setup_dismissal_resets_with_the_authenticated_session(self):
        js_response = self.client.get("/dashboard/app.js")

        set_session_source = js_response.text.split("function setSession", maxsplit=1)[
            1
        ].split("function clearSession", maxsplit=1)[0]
        clear_session_source = js_response.text.split(
            "function clearSession", maxsplit=1
        )[1].split("function logout", maxsplit=1)[0]
        for source in (set_session_source, clear_session_source):
            self.assertIn("state.companyValidation", source)
            self.assertIn("status: null", source)
            self.assertIn("error: false", source)
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
