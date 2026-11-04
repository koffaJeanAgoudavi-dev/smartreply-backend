"""Tests de publication de la politique de confidentialité."""

import re
import unittest

from fastapi.testclient import TestClient

from app.main import app


class PrivacyStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_privacy_policy_is_public_with_a_canonical_trailing_slash(self):
        redirect = self.client.get("/confidentialite", follow_redirects=False)
        page = self.client.get("/confidentialite/")

        self.assertEqual(redirect.status_code, 307)
        self.assertEqual(redirect.headers["location"], "/confidentialite/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn('lang="fr"', page.text)
        self.assertIn("Politique de confidentialité", page.text)
        self.assertIn('<time datetime="2026-11-04">4 novembre 2026</time>', page.text)
        self.assertIn("Version : programme pilote", page.text)

    def test_policy_contains_the_thirteen_approved_sections(self):
        page = self.client.get("/confidentialite/")
        expected_sections = {
            "responsable": "Qui sommes-nous ?",
            "objet": "Objet de cette politique",
            "pilote": "Fonctionnement du programme pilote",
            "donnees": "Données que nous utilisons",
            "gemini": "Utilisation de Gemini",
            "finalites": "Base et finalités du traitement",
            "acces": "Accès aux données",
            "securite": "Sécurité",
            "conservation": "Conservation des données",
            "suppression": "Suppression des données",
            "droits": "Vos droits",
            "modifications": "Modifications de cette politique",
            "contact": "Contact",
        }

        self.assertEqual(page.text.count('class="legal-section'), 13)
        for section_id, heading in expected_sections.items():
            with self.subTest(section=section_id):
                self.assertIn(f'id="{section_id}"', page.text)
                self.assertIn(heading, page.text)

        for approved_claim in (
            "10 entreprises sélectionnées",
            "quatre personnes ayant participé à des tests internes",
            "sous trois jours et au plus tard sous sept jours",
            "quota gratuit de Gemini",
        ):
            self.assertIn(approved_claim, page.text)

    def test_policy_matches_company_first_onboarding_without_regional_restriction(self):
        page = self.client.get("/confidentialite/")
        approved_flow = (
            "l’utilisateur crée un compte SmartReply",
            "il renseigne le nom et le secteur de son entreprise",
            "il autorise Google Drive",
            "SmartReply crée l’entreprise et le Google Sheet nommé",
            "il connecte Gmail et peut connecter Telegram",
            "DIGICRAFT LABS vérifie et valide manuellement l’entreprise",
            "le traitement des e-mails commence après cette validation",
        )
        flow_positions = [page.text.index(step) for step in approved_flow]

        self.assertEqual(flow_positions, sorted(flow_positions))
        self.assertNotIn("Espace économique européen", page.text)
        self.assertNotIn("avant l’ajout de l’entreprise à l’allowlist", page.text)

    def test_technical_disclosures_match_the_current_implementation(self):
        page = self.client.get("/confidentialite/")

        for disclosure in (
            "la date de création du compte",
            "ton de réponse choisi",
            "statut administratif n’est pas stocké dans une colonne dédiée",
            "calculé à partir de l’allowlist interne",
            "refresh tokens Gmail et Google Drive sont chiffrés",
            "limite par défaut du corps d’e-mail transmis est d’environ "
            "6 000 caractères",
            "L’application n’enregistre pas directement ces champs dans sa "
            "base PostgreSQL",
            "empreinte HMAC utilisée dans des compteurs temporaires",
        ):
            self.assertIn(disclosure, page.text)

        self.assertNotIn("les dates de création et de connexion", page.text)
        self.assertIn("le nom de l’entreprise", page.text)
        self.assertIn("le motif, l’objet et le message", page.text)

    def test_gemini_disclosure_is_cautious_and_does_not_promise_provider_terms(self):
        page = self.client.get("/confidentialite/")

        self.assertIn(
            "dépendent du service utilisé, du compte, de la facturation et des "
            "conditions Google applicables",
            page.text,
        )
        self.assertIn("Elles peuvent évoluer", page.text)
        self.assertIn(
            "configuration payante Gemini après le programme pilote",
            page.text,
        )
        self.assertNotIn("Des évaluateurs humains", page.text)
        self.assertNotIn(
            "ne sont pas utilisés pour améliorer les produits Google",
            page.text,
        )

    def test_security_address_and_internal_publication_note_are_visible(self):
        page = self.client.get("/confidentialite/")

        self.assertIn("Note de publication", page.text)
        self.assertIn("Avant de publier", page.text)
        self.assertIn("security.smartreplyagent@gmail.com", page.text)
        self.assertIn("confirmer les régions d’accès au pilote", page.text)
        self.assertIn('href="mailto:hello.smartreplyagent@gmail.com"', page.text)
        self.assertIn(
            'href="mailto:security.smartreplyagent@gmail.com"', page.text
        )

    def test_assets_navigation_and_contact_scope_are_safe_and_local(self):
        page = self.client.get("/confidentialite/")
        stylesheet = self.client.get("/conditions/styles.css")
        contact = self.client.get("/contact/")

        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn('href="#main-content"', page.text)
        self.assertIn('aria-label="Sommaire de la politique', page.text)
        self.assertIn('href="/conditions/"', page.text)
        self.assertIn('href="/contact/"', page.text)
        self.assertIn('aria-current="page" href="/confidentialite/"', page.text)
        self.assertIn(".legal-subsection", stylesheet.text)
        self.assertNotIn("style=", page.text)
        self.assertNotRegex(
            page.text,
            re.compile(
                r'<(?:script|link)[^>]+(?:src|href)=["\']https?://',
                re.IGNORECASE,
            ),
        )
        self.assertNotIn('href="/confidentialite/"', contact.text)


if __name__ == "__main__":
    unittest.main()
