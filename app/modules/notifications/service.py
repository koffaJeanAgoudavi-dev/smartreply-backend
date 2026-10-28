"""
SmartReply Agent — MODULE 6 : Notifications Telegram
=====================================================
Envoie le message récapitulatif d'un email analysé + bouton
« Voir le brouillon » (lien direct vers le brouillon Gmail, §5.1).

Le traitement des callbacks (Envoyer / Ignorer) relève du Module 7.
"""
import logging

import httpx

from app.core.config import settings


class _TelegramTokenRedactionFilter(logging.Filter):
    """Empêche le token présent dans l'URL Bot API d'atteindre les logs httpx."""

    def filter(self, record: logging.LogRecord) -> bool:
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            return True
        if isinstance(record.msg, str):
            record.msg = record.msg.replace(token, "[REDACTED]")
        if isinstance(record.args, tuple):
            record.args = tuple(
                str(value).replace(token, "[REDACTED]")
                if token in str(value)
                else value
                for value in record.args
            )
        return True


logging.getLogger("httpx").addFilter(_TelegramTokenRedactionFilter())

TELEGRAM_API_URL = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"
DRAFT_LINK = "https://mail.google.com/mail/u/0/#drafts/{draft_id}"


async def send_email_notification(
    chat_id: int,
    expediteur: str,
    objet: str,
    resume: str,
    categorie: str,
    priorite: str,
    email_id: str,
    draft_id: str,
) -> dict:
    """
    Envoie la notification récapitulative d'un email analysé.
    Retourne la réponse brute de l'API Telegram.
    """
    texte = (
        "⚡ *SmartReply Agent*\n"
        "📧 *Nouvel email analysé*\n\n"
        f"*De :* {expediteur}\n"
        f"*Objet :* {objet}\n"
        f"*Catégorie :* {categorie}\n"
        f"*Priorité :* {priorite}\n"
        f"*Résumé :* {resume}\n"
        "*Statut :* Brouillon prêt ✅"
    )

    payload = {
        "chat_id": chat_id,
        "text": texte,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
                "reply_markup": {
            "inline_keyboard": [[
                {"text": "👁 Voir", "url": DRAFT_LINK.format(draft_id=draft_id)},
                {"text": "✅ Envoyer", "callback_data": f"send:{email_id}:{draft_id}"},
                {"text": "🗑 Ignorer", "callback_data": f"ignore:{email_id}:{draft_id}"},
            ]]
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Envoi Telegram échoué (HTTP {resp.status_code}): {resp.text}")
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Envoi Telegram refusé : {data.get('description')}")
        return data["result"]


class CompanyTelegramError(RuntimeError):
    """Erreur Telegram sûre, sans réponse fournisseur ni identifiant."""

    def __init__(self, code: str, *, status_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class CompanyTelegramBlockedError(CompanyTelegramError):
    """Le bot a été bloqué par le destinataire (HTTP 403)."""


def _truncate_plain_message(text: str, *, limit: int = 4096) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


async def _company_telegram_request(method: str, payload: dict) -> dict:
    if not settings.TELEGRAM_BOT_TOKEN:
        raise CompanyTelegramError("telegram_not_configured")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{TELEGRAM_API_URL}/{method}", json=payload)
    except httpx.HTTPError as exc:
        raise CompanyTelegramError("telegram_transport_error") from exc
    if response.status_code == 403:
        raise CompanyTelegramBlockedError("telegram_blocked", status_code=403)
    if response.status_code != 200:
        raise CompanyTelegramError(
            f"telegram_http_{response.status_code}",
            status_code=response.status_code,
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise CompanyTelegramError("telegram_response_invalid") from exc
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise CompanyTelegramError("telegram_response_rejected")
    return data


async def send_company_email_notification(
    chat_id: int,
    *,
    expediteur: str,
    objet: str,
    categorie: str,
    priorite: str,
    resume: str,
    view_callback_data: str,
) -> None:
    """Notification multi-entreprise parallèle au wrapper historique."""
    if len(view_callback_data.encode("utf-8")) > 64:
        raise ValueError("telegram_callback_data_too_long")
    text = _truncate_plain_message(
        "SmartReply Agent\n"
        "Nouvel email analysé\n\n"
        f"De : {expediteur}\n"
        f"Objet : {objet}\n"
        f"Catégorie : {categorie}\n"
        f"Priorité : {priorite}\n"
        f"Résumé : {resume}\n"
        "Statut : Brouillon prêt"
    )
    await _company_telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": "Voir le brouillon",
                            "callback_data": view_callback_data,
                        }
                    ]
                ]
            },
        },
    )


async def send_company_telegram_message(
    chat_id: int,
    text: str,
    *,
    reply_markup: dict | None = None,
) -> None:
    payload: dict = {
        "chat_id": chat_id,
        "text": _truncate_plain_message(text),
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    await _company_telegram_request("sendMessage", payload)


async def answer_company_callback(callback_id: str, text: str) -> None:
    await _company_telegram_request(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text[:200],
            "show_alert": False,
        },
    )


async def send_company_gmail_revoked_notification(chat_id: int) -> None:
    dashboard_url = settings.BASE_URL.rstrip("/") + "/dashboard/#integrations"
    await send_company_telegram_message(
        chat_id,
        "Votre connexion Gmail a expiré. Reconnectez-la depuis la section "
        f"Intégrations : {dashboard_url}",
    )
