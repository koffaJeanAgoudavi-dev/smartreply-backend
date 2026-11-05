"""Tests de la page publique Contact et de sa livraison SMTP."""

import asyncio
import json
import smtplib
import struct
import subprocess
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.rate_limit import InMemoryRateLimiter, get_rate_limiter
from app.main import app
from app.modules.contact.schemas import ContactRequest
from app.modules.contact.service import ContactDeliveryError, deliver_contact

CONTACT_PAYLOAD = {
    "full_name": "Alice Exemple",
    "email": "alice@example.com",
    "subject": "Question sur SmartReply",
    "reason": "Information sur le produit",
    "message": "Je souhaite obtenir davantage d’informations sur le produit.",
}


class ContactPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_contact_redirect_and_validated_content_are_served(self):
        redirect = self.client.get("/contact", follow_redirects=False)
        page = self.client.get("/contact/")

        self.assertEqual(redirect.status_code, 307)
        self.assertEqual(redirect.headers["location"], "/contact/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn('lang="fr"', page.text)
        for content in (
            "Contact et assistance SmartReply",
            "DIGICRAFT LABS",
            "Choisissez le bon canal",
            "Produit, pilote et offres",
            "Assistance technique",
            "Données et suppression",
            "Incident de sécurité",
            "security.smartreplyagent@gmail.com",
            "Une réponse est visée sous 24 heures",
            "au plus tard sous sept jours",
            "Envoyez-nous un message",
            "Information sur le produit",
            "Utilisation et assistance",
            "Offre et facturation",
            "Partenariat",
            "Vous préférez écrire directement ?",
            "hello.smartreplyagent@gmail.com",
            "Merci pour votre message !",
        ):
            self.assertIn(content, page.text)
        for field in ("full_name", "email", "subject", "reason", "message"):
            self.assertIn(f'name="{field}"', page.text)
        self.assertEqual(page.text.count('class="feature-badge-new"'), 1)
        self.assertIn('href="/dashboard/"', page.text)
        self.assertIn('href="/conditions/"', page.text)
        self.assertIn('href="/confidentialite/"', page.text)
        self.assertIn('aria-current="page" href="/contact/"', page.text)
        self.assertIn("ne sont pas enregistrées directement", page.text)
        self.assertIn("protection anti-abus temporaire", page.text)
        self.assertIn('id="contact-success"', page.text)
        self.assertIn('id="contact-success" class="contact-success"', page.text)
        success_markup = page.text.split('id="contact-success"', maxsplit=1)[1]
        self.assertTrue(success_markup.split(">", maxsplit=1)[0].endswith(" hidden"))
        self.assertNotRegex(page.text, r'(?:src|href)="https?://')

    def test_contact_assets_are_local_responsive_and_success_is_gated(self):
        css = self.client.get("/contact/styles.css")
        javascript = self.client.get("/contact/app.js")
        mark = self.client.get(
            "/dashboard/assets/brand/smartreply-mark-standard.png"
        )

        self.assertEqual(css.status_code, 200)
        self.assertIn("text/css", css.headers["content-type"])
        self.assertIn("@media (min-width: 620px)", css.text)
        self.assertIn("@media (min-width: 760px)", css.text)
        self.assertIn(".contact-channels", css.text)
        self.assertIn(".contact-process-steps", css.text)
        self.assertIn(".contact-footer", css.text)
        self.assertIn("grid-template-columns", css.text)
        self.assertEqual(javascript.status_code, 200)
        self.assertIn("javascript", javascript.headers["content-type"])
        self.assertIn('fetch("/api/v1/contact"', javascript.text)
        self.assertIn("if (!response.ok)", javascript.text)
        self.assertIn('result.status !== "sent"', javascript.text)
        self.assertIn("success.hidden = false", javascript.text)
        self.assertEqual(mark.status_code, 200)
        self.assertEqual(mark.content[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", mark.content[16:24]), (245, 245))

    def test_browser_confirmation_only_follows_a_successful_response(self):
        repository_root = Path(__file__).resolve().parents[1]
        script = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("app/contact/app.js", "utf8");
let submitHandler = null;
const submitLabel = { textContent: "Envoyer le message" };
const form = {
  hidden: false,
  reportValidity: () => true,
  reset() {},
  setAttribute() {},
  addEventListener: (_event, handler) => { submitHandler = handler; },
};
const error = { hidden: true, textContent: "", focus() {} };
const success = { hidden: true, focus() {} };
const submit = { disabled: false, querySelector: () => submitLabel };
const elements = {
  "contact-form": form,
  "contact-error": error,
  "contact-success": success,
  "contact-submit": submit,
};
let response = { ok: false, status: 503, json: async () => ({}) };
const context = {
  document: { getElementById: (id) => elements[id] },
  FormData: class { get(name) { return name; } },
  fetch: async () => response,
  JSON,
};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
  await submitHandler({ preventDefault() {} });
  const failed = {
    formHidden: form.hidden,
    successHidden: success.hidden,
    errorHidden: error.hidden,
  };
  form.hidden = false;
  success.hidden = true;
  error.hidden = true;
  response = { ok: true, status: 200, json: async () => ({ status: "sent" }) };
  await submitHandler({ preventDefault() {} });
  process.stdout.write(JSON.stringify({
    failed,
    sent: {
      formHidden: form.hidden,
      successHidden: success.hidden,
      errorHidden: error.hidden,
    },
  }));
})();
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
            result["failed"],
            {"formHidden": False, "successHidden": True, "errorHidden": False},
        )
        self.assertEqual(
            result["sent"],
            {"formHidden": True, "successHidden": False, "errorHidden": True},
        )


class ContactApiTests(unittest.TestCase):
    def setUp(self):
        self.original_settings = {
            "CLIENT_IP_HEADER": settings.CLIENT_IP_HEADER,
            "CONTACT_IP_RATE_LIMIT": settings.CONTACT_IP_RATE_LIMIT,
            "CONTACT_RATE_LIMIT_WINDOW_SECONDS": (
                settings.CONTACT_RATE_LIMIT_WINDOW_SECONDS
            ),
        }
        settings.CLIENT_IP_HEADER = "X-Test-Client-IP"
        settings.CONTACT_IP_RATE_LIMIT = 5
        settings.CONTACT_RATE_LIMIT_WINDOW_SECONDS = 3600
        self.limiter = InMemoryRateLimiter(
            "contact-test-rate-limit-secret-at-least-32-bytes"
        )
        app.dependency_overrides[get_rate_limiter] = lambda: self.limiter
        self.client = TestClient(app)
        self.headers = {"X-Test-Client-IP": "192.0.2.60"}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        for name, value in self.original_settings.items():
            setattr(settings, name, value)

    def test_success_is_returned_only_after_delivery(self):
        delivery = AsyncMock()
        with patch("app.modules.contact.router.deliver_contact", delivery):
            response = self.client.post(
                "/api/v1/contact",
                json=CONTACT_PAYLOAD,
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "sent"})
        delivery.assert_awaited_once()
        delivered = delivery.await_args.args[0]
        self.assertEqual(delivered.full_name, CONTACT_PAYLOAD["full_name"])
        self.assertEqual(str(delivered.email), CONTACT_PAYLOAD["email"])

    def test_delivery_failure_is_clear_and_never_confirms(self):
        delivery = AsyncMock(side_effect=ContactDeliveryError("smtp_failed"))
        with (
            patch("app.modules.contact.router.deliver_contact", delivery),
            patch("app.modules.contact.router.logger.warning"),
        ):
            response = self.client.post(
                "/api/v1/contact",
                json=CONTACT_PAYLOAD,
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "detail": (
                    "Votre message n’a pas pu être envoyé. "
                    "Veuillez réessayer dans quelques instants."
                )
            },
        )
        self.assertNotIn("sent", response.text)

    def test_invalid_payload_is_rejected_without_delivery(self):
        delivery = AsyncMock()
        payload = {**CONTACT_PAYLOAD, "email": "adresse-invalide", "message": "court"}
        with patch("app.modules.contact.router.deliver_contact", delivery):
            response = self.client.post(
                "/api/v1/contact",
                json=payload,
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 422)
        delivery.assert_not_awaited()

    def test_contact_rate_limit_blocks_before_delivery(self):
        settings.CONTACT_IP_RATE_LIMIT = 2
        delivery = AsyncMock()
        with patch("app.modules.contact.router.deliver_contact", delivery):
            responses = [
                self.client.post(
                    "/api/v1/contact",
                    json=CONTACT_PAYLOAD,
                    headers=self.headers,
                )
                for _ in range(3)
            ]

        self.assertEqual(
            [response.status_code for response in responses],
            [200, 200, 429],
        )
        self.assertEqual(delivery.await_count, 2)
        self.assertEqual(responses[2].headers["Retry-After"], "3600")


class ContactSmtpServiceTests(unittest.TestCase):
    def setUp(self):
        self.original_settings = {
            "CONTACT_RECIPIENT_EMAIL": settings.CONTACT_RECIPIENT_EMAIL,
            "CONTACT_SMTP_HOST": settings.CONTACT_SMTP_HOST,
            "CONTACT_SMTP_PORT": settings.CONTACT_SMTP_PORT,
            "CONTACT_SMTP_USERNAME": settings.CONTACT_SMTP_USERNAME,
            "CONTACT_SMTP_PASSWORD": settings.CONTACT_SMTP_PASSWORD,
            "CONTACT_SMTP_TIMEOUT_SECONDS": settings.CONTACT_SMTP_TIMEOUT_SECONDS,
        }
        settings.CONTACT_RECIPIENT_EMAIL = "hello.smartreplyagent@gmail.com"
        settings.CONTACT_SMTP_HOST = "smtp.gmail.com"
        settings.CONTACT_SMTP_PORT = 465
        settings.CONTACT_SMTP_USERNAME = "sender@example.com"
        settings.CONTACT_SMTP_PASSWORD = "application-password"
        settings.CONTACT_SMTP_TIMEOUT_SECONDS = 15
        self.payload = ContactRequest.model_validate(CONTACT_PAYLOAD)

    def tearDown(self):
        for name, value in self.original_settings.items():
            setattr(settings, name, value)

    def test_smtp_delivery_uses_authenticated_sender_and_reply_to(self):
        with patch("app.modules.contact.service.smtplib.SMTP_SSL") as smtp_class:
            smtp = smtp_class.return_value.__enter__.return_value
            asyncio.run(deliver_contact(self.payload))

        smtp_class.assert_called_once()
        _, port = smtp_class.call_args.args
        self.assertEqual(port, 465)
        self.assertEqual(smtp_class.call_args.args[0], "smtp.gmail.com")
        self.assertEqual(smtp_class.call_args.kwargs["timeout"], 15)
        smtp.login.assert_called_once_with(
            "sender@example.com",
            "application-password",
        )
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "hello.smartreplyagent@gmail.com")
        self.assertEqual(message["Reply-To"], "alice@example.com")
        self.assertEqual(
            message["Subject"],
            "[Contact SmartReply Agent] Question sur SmartReply",
        )
        self.assertIn("Alice Exemple", message.get_content())
        self.assertIn("Information sur le produit", message.get_content())
        self.assertEqual(
            smtp.send_message.call_args.kwargs["to_addrs"],
            ["hello.smartreplyagent@gmail.com"],
        )

    def test_missing_credentials_and_smtp_errors_fail_closed(self):
        settings.CONTACT_SMTP_PASSWORD = ""
        with self.assertRaisesRegex(
            ContactDeliveryError,
            "contact_smtp_credentials_missing",
        ):
            asyncio.run(deliver_contact(self.payload))

        settings.CONTACT_SMTP_PASSWORD = "application-password"
        with patch("app.modules.contact.service.smtplib.SMTP_SSL") as smtp_class:
            smtp = smtp_class.return_value.__enter__.return_value
            smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"denied")
            with self.assertRaisesRegex(
                ContactDeliveryError,
                "contact_smtp_delivery_failed",
            ):
                asyncio.run(deliver_contact(self.payload))


if __name__ == "__main__":
    unittest.main()
