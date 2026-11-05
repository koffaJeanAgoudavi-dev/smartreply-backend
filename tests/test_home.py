"""Tests de la page d'accueil publique de SmartReply Agent."""

import re
import unittest
from html.parser import HTMLParser

from fastapi.testclient import TestClient

from app.main import app


class _DocumentInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.local_fragments: list[str] = []
        self.heading_levels: list[int] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.ids.append(element_id)
        href = attributes.get("href")
        if href and href.startswith("#"):
            self.local_fragments.append(href[1:])
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.heading_levels.append(int(tag[1]))


class HomePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def test_root_serves_the_public_french_homepage(self):
        page = self.client.get("/")

        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn('lang="fr"', page.text)
        self.assertIn(
            "Moins de temps dans votre boîte mail. ",
            page.text,
        )
        self.assertIn("Plus de temps pour votre activité.", page.text)
        self.assertIn("Vous vérifiez, vous décidez.", page.text)
        self.assertIn("De l’email reçu au brouillon prêt à vérifier.", page.text)
        self.assertIn(
            "Un assistant qui prépare. Un professionnel qui décide.",
            page.text,
        )
        self.assertIn("DIGICRAFT LABS", page.text)
        self.assertEqual(page.text.count('class="new-badge'), 2)

    def test_homepage_has_both_pilot_and_existing_user_paths(self):
        page = self.client.get("/")

        self.assertGreaterEqual(page.text.count('href="/dashboard/"'), 4)
        self.assertGreaterEqual(
            page.text.count('href="/contact/#contact-form-title"'),
            3,
        )
        self.assertIn("Accéder à SmartReply", page.text)
        self.assertIn("Demander l’accès au pilote", page.text)
        self.assertIn("J’ai déjà un accès", page.text)
        self.assertIn('href="/contact/"', page.text)
        self.assertIn('href="/conditions/"', page.text)
        self.assertIn('href="/confidentialite/"', page.text)

    def test_marketing_claims_match_the_current_product_boundaries(self):
        page = self.client.get("/")

        for approved_claim in (
            "Gmail + Google Sheets requis",
            "Telegram peut être ajouté ensuite",
            "Aucun envoi automatique",
            "Pas d’envoi automatique depuis le dashboard",
            "Votre Google Sheet vous appartient",
            "Telegram reste un choix",
            "sur invitation",
        ):
            self.assertIn(approved_claim, page.text)

        for forbidden_claim in (
            "répond automatiquement à vos clients",
            "élimine tous les spams",
            "sécurité garantie",
            "gratuit pour tous",
            "envoyez directement depuis le dashboard",
        ):
            self.assertNotIn(forbidden_claim, page.text.lower())

    def test_homepage_assets_are_local_responsive_and_csp_compatible(self):
        page = self.client.get("/")
        stylesheet = self.client.get("/home/styles.css")
        mark = self.client.get(
            "/dashboard/assets/brand/smartreply-mark-standard.png"
        )

        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn("text/css", stylesheet.headers["content-type"])
        self.assertEqual(mark.status_code, 200)
        self.assertIn("@media (max-width: 880px)", stylesheet.text)
        self.assertIn("@media (max-width: 640px)", stylesheet.text)
        self.assertIn("@media (prefers-reduced-motion: reduce)", stylesheet.text)
        self.assertIn(".hero-layout", stylesheet.text)
        self.assertIn(".control-section", stylesheet.text)
        self.assertIn(".pilot-card", stylesheet.text)
        self.assertNotIn("style=", page.text)
        self.assertNotRegex(
            page.text,
            re.compile(
                r'<(?:script|link|img)[^>]+(?:src|href)=["\']https?://',
                re.IGNORECASE,
            ),
        )

    def test_homepage_structure_has_unique_ids_and_valid_local_fragments(self):
        page = self.client.get("/")
        inspector = _DocumentInspector()
        inspector.feed(page.text)

        self.assertEqual(len(inspector.ids), len(set(inspector.ids)))
        self.assertTrue(set(inspector.local_fragments).issubset(set(inspector.ids)))
        self.assertEqual(inspector.heading_levels.count(1), 1)
        self.assertEqual(inspector.heading_levels[0], 1)
        self.assertIn("main-content", inspector.ids)
        self.assertIn("fonctionnement", inspector.ids)
        self.assertIn("benefices", inspector.ids)
        self.assertIn("controle", inspector.ids)
        self.assertIn("pilote", inspector.ids)

    def test_public_pages_link_back_to_the_new_homepage(self):
        for path in ("/contact/", "/conditions/", "/confidentialite/"):
            with self.subTest(path=path):
                page = self.client.get(path)
                self.assertEqual(page.status_code, 200)
                self.assertIn(
                    'href="/" aria-label="SmartReply Agent — Accueil public"',
                    page.text,
                )
                self.assertIn('<a href="/">Accueil SmartReply</a>', page.text)

        dashboard = self.client.get("/dashboard/")
        self.assertIn('<a href="/">Accueil public</a>', dashboard.text)


if __name__ == "__main__":
    unittest.main()
