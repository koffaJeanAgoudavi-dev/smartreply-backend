"""Tests de publication des conditions générales d'utilisation."""

import re
import unittest

from fastapi.testclient import TestClient

from app.main import app


class ConditionsStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_conditions_are_public_and_canonical_path_has_a_trailing_slash(self):
        redirect = self.client.get("/conditions", follow_redirects=False)
        page = self.client.get("/conditions/")

        self.assertEqual(redirect.status_code, 307)
        self.assertEqual(redirect.headers["location"], "/conditions/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn('lang="fr"', page.text)
        self.assertIn("Conditions générales d’utilisation", page.text)
        self.assertIn('<time datetime="2026-11-04">4 novembre 2026</time>', page.text)
        self.assertIn("Version : programme pilote", page.text)

    def test_published_document_contains_the_twenty_approved_sections(self):
        page = self.client.get("/conditions/")
        expected_sections = {
            "objet": "Objet",
            "definitions": "Définitions",
            "acces": "Conditions d’accès",
            "pilote": "Programme pilote",
            "validation": "Création et validation de l’entreprise",
            "gmail": "Connexion de Gmail",
            "sheets": "Google Sheets",
            "telegram": "Telegram facultatif",
            "ia": "Intelligence artificielle",
            "usages-interdits": "Usages interdits ou exclus du pilote",
            "responsabilites": "Responsabilités de l’utilisateur",
            "propriete": "Accès et propriété du service",
            "disponibilite": "Disponibilité et évolution du service",
            "suspension": "Suspension et fin d’accès",
            "suppression": "Suppression sur demande",
            "prix": "Prix et conditions du pilote",
            "support": "Support",
            "modifications": "Modifications des CGU",
            "droit-applicable": "Droit applicable et règlement des difficultés",
            "editeur": "Contact de l’éditeur",
        }

        self.assertEqual(page.text.count('class="legal-section'), 20)
        for section_id, heading in expected_sections.items():
            with self.subTest(section=section_id):
                self.assertIn(f'id="{section_id}"', page.text)
                self.assertIn(heading, page.text)

        for approved_claim in (
            "10 entreprises externes",
            "quatre personnes ayant participé à des tests internes",
            "premier mois gratuit",
            "sous trois jours et au plus tard sous sept jours",
            "réponse sous 24 heures",
            "quota gratuit de Gemini",
        ):
            self.assertIn(approved_claim, page.text)

        approved_flow = (
            "Création du compte",
            "Renseignement du nom et du secteur de l’entreprise",
            "Autorisation de Google Drive",
            "Création de l’entreprise et de son Google Sheet nommé",
            "Connexion des autres services nécessaires",
            "Statut EN ATTENTE",
            "Validation manuelle par DIGICRAFT LABS",
            "Statut VALIDÉE puis début du traitement autorisé",
        )
        flow_positions = [page.text.index(step) for step in approved_flow]
        self.assertEqual(flow_positions, sorted(flow_positions))
        self.assertIn(
            "configuration payante Gemini après le programme pilote",
            page.text,
        )
        self.assertIn(
            "Pour toute nouvelle entreprise créée avec le parcours actuel",
            page.text,
        )
        self.assertIn(
            "peuvent rester détenus par le compte technique de DIGICRAFT LABS",
            page.text,
        )
        self.assertIn(
            "consulter et copier le contenu préparé, ouvrir Gmail et marquer un "
            "brouillon comme ignoré",
            page.text,
        )
        self.assertIn("ne déclenchent aucun envoi", page.text)
        self.assertIn("Le dashboard ne propose pas d’envoi direct", page.text)

    def test_confirmed_publication_details_replace_the_internal_note(self):
        page = self.client.get("/conditions/")

        self.assertNotIn("Note de publication", page.text)
        self.assertNotIn("Suivi interne", page.text)
        self.assertNotIn("Si cette adresse n’est pas encore active", page.text)
        self.assertNotIn("doivent être relues", page.text)
        self.assertIn("security.smartreplyagent@gmail.com", page.text)
        self.assertIn("utilise actuellement le quota gratuit de Gemini", page.text)
        self.assertIn('href="mailto:hello.smartreplyagent@gmail.com"', page.text)
        self.assertIn(
            'href="mailto:security.smartreplyagent@gmail.com"', page.text
        )

    def test_ten_external_pilot_companies_keep_fair_use_free_access(self):
        page = self.client.get("/conditions/")

        for approved_term in (
            "registre privé du programme",
            "pendant toute la durée d’exploitation de SmartReply",
            "accès sans frais d’abonnement",
            "d’un usage raisonnable",
            "coûts disproportionnés",
            "avant d’appliquer une restriction",
            "urgence liée à la sécurité",
        ):
            self.assertIn(approved_term, page.text)

        self.assertIn(
            "Ils ne bénéficient pas automatiquement de la garantie accordée",
            page.text,
        )

    def test_selected_privacy_links_and_local_navigation_are_preserved(self):
        page = self.client.get("/conditions/")
        privacy_redirect = self.client.get(
            "/confidentialite", follow_redirects=False
        )
        privacy_page = self.client.get("/confidentialite/")

        self.assertEqual(page.text.count('href="/confidentialite"'), 2)
        self.assertIn('href="/confidentialite/"', page.text)
        self.assertEqual(privacy_redirect.status_code, 307)
        self.assertEqual(privacy_redirect.headers["location"], "/confidentialite/")
        self.assertEqual(privacy_page.status_code, 200)
        self.assertIn('href="/contact/"', page.text)
        self.assertIn('href="/dashboard/"', page.text)
        self.assertIn('aria-current="page" href="/conditions/"', page.text)
        self.assertNotRegex(
            page.text,
            re.compile(
                r'<(?:script|link)[^>]+(?:src|href)=["\']https?://',
                re.IGNORECASE,
            ),
        )

    def test_conditions_styles_are_local_responsive_and_accessible(self):
        page = self.client.get("/conditions/")
        stylesheet = self.client.get("/conditions/styles.css")

        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn('href="#main-content"', page.text)
        self.assertIn('aria-label="Sommaire des conditions générales', page.text)
        self.assertIn(".legal-page", stylesheet.text)
        self.assertIn(".legal-toc", stylesheet.text)
        self.assertIn(".legal-section", stylesheet.text)
        self.assertIn("@media (min-width: 540px)", stylesheet.text)
        self.assertIn("@media (min-width: 980px)", stylesheet.text)
        self.assertIn("@media (prefers-reduced-motion: reduce)", stylesheet.text)
        self.assertNotIn(".legal-publication-note", stylesheet.text)
        self.assertNotIn("style=", page.text)


if __name__ == "__main__":
    unittest.main()
