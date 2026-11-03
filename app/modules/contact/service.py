"""Livraison SMTP des demandes reçues depuis la page Contact."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from email_validator import EmailNotValidError, validate_email

from app.core.config import settings
from app.modules.contact.schemas import ContactRequest


class ContactDeliveryError(RuntimeError):
    """Erreur d'envoi sûre, sans donnée personnelle ni secret."""


def _configured_address(value: str) -> str:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ContactDeliveryError("contact_email_configuration_invalid") from exc


def _build_message(payload: ContactRequest) -> tuple[EmailMessage, str, str]:
    username = _configured_address(settings.CONTACT_SMTP_USERNAME)
    recipient = _configured_address(settings.CONTACT_RECIPIENT_EMAIL)
    if not settings.CONTACT_SMTP_PASSWORD:
        raise ContactDeliveryError("contact_smtp_credentials_missing")

    message = EmailMessage()
    message["From"] = formataddr(("SmartReply Agent", username))
    message["To"] = recipient
    message["Reply-To"] = str(payload.email)
    message["Subject"] = f"[Contact SmartReply Agent] {payload.subject}"
    message.set_content(
        "\n".join(
            (
                "Nouvelle demande depuis la page Contact SmartReply Agent",
                "",
                f"Nom complet : {payload.full_name}",
                f"Adresse email : {payload.email}",
                f"Motif du contact : {payload.reason.value}",
                f"Objet : {payload.subject}",
                "",
                "Message :",
                payload.message,
            )
        )
    )
    return message, username, recipient


def _send_message(payload: ContactRequest) -> None:
    message, username, recipient = _build_message(payload)
    host = settings.CONTACT_SMTP_HOST.strip()
    port = settings.CONTACT_SMTP_PORT
    timeout = settings.CONTACT_SMTP_TIMEOUT_SECONDS
    if not host or port <= 0 or timeout <= 0:
        raise ContactDeliveryError("contact_smtp_configuration_invalid")

    try:
        with smtplib.SMTP_SSL(
            host,
            port,
            timeout=timeout,
            context=ssl.create_default_context(),
        ) as smtp:
            smtp.login(username, settings.CONTACT_SMTP_PASSWORD)
            smtp.send_message(
                message,
                from_addr=username,
                to_addrs=[recipient],
            )
    except (OSError, smtplib.SMTPException, TimeoutError) as exc:
        raise ContactDeliveryError("contact_smtp_delivery_failed") from exc


async def deliver_contact(payload: ContactRequest) -> None:
    """Attend la confirmation SMTP avant de confirmer l'envoi au navigateur."""

    await asyncio.to_thread(_send_message, payload)
