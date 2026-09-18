"""
SmartReply Agent — MODULE 6 : Notifications Telegram
=====================================================
Envoie le message récapitulatif d'un email analysé + bouton
« Voir le brouillon » (lien direct vers le brouillon Gmail, §5.1).

Le traitement des callbacks (Envoyer / Ignorer) relève du Module 7.
"""
import httpx
from app.core.config import settings

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
