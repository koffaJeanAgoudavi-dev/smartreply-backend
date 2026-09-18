"""
SmartReply Agent — MODULE 7 : Actions utilisateur (callbacks Telegram)
======================================================================
Traite les callbacks des boutons sous les notifications :
  - Envoyer : envoie le brouillon via l'API Gmail + statuts à « Envoyé »
  - Ignorer : statuts à « Ignoré » sans envoi

Statuts figés (§8.2). callback_data format : "action:email_id:brouillon_id"
"""
import traceback
import httpx
from app.core.config import settings
from app.modules.gmail.service import _get_access_token
from app.modules.sheets.service import get_data_workbook

TELEGRAM_API_URL = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"


async def _answer_callback(callback_id: str, text: str):
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": False,
        })


async def _send_message(chat_id: int, text: str):
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        })


async def send_draft(draft_id: str):
    """Envoie un brouillon Gmail existant (API Gmail drafts.send)."""
    access_token = await _get_access_token()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts/send",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"id": draft_id},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Envoi brouillon échoué (HTTP {resp.status_code}): {resp.text}")
        if resp.status_code != 200:
            raise RuntimeError(f"Envoi brouillon échoué (HTTP {resp.status_code}): {resp.text}")


def update_statuts(email_id: str, brouillon_id: str, statut: str):
    """Met à jour Statut dans Brouillons (col 4) et Emails (col 7)."""
    wb = get_data_workbook()

    sheet_b = wb.worksheet("Brouillons")
    for idx, rec in enumerate(sheet_b.get_all_records(), start=2):
        if str(rec.get("Brouillon_ID")) == str(brouillon_id):
            sheet_b.update_cell(idx, 4, statut)
            break

    sheet_e = wb.worksheet("Emails")
    for idx, rec in enumerate(sheet_e.get_all_records(), start=2):
        if str(rec.get("Email_ID")) == str(email_id):
            sheet_e.update_cell(idx, 7, statut)
            break


async def handle_envoyer(chat_id: int, email_id: str, draft_id: str, callback_id: str):
    await _answer_callback(callback_id, "Envoi en cours…")
    try:
        await send_draft(draft_id)
        update_statuts(email_id, draft_id, "Envoyé")
        await _send_message(chat_id, f"✅ *Brouillon envoyé !*\n\n`{email_id}` → statut « Envoyé » dans votre Sheet.")
    except Exception:
        traceback.print_exc()
        await _send_message(chat_id, "⚠️ L'envoi a échoué (voir logs Render). Le brouillon reste disponible dans Gmail.")


async def handle_ignorer(chat_id: int, email_id: str, draft_id: str, callback_id: str):
    update_statuts(email_id, draft_id, "Ignoré")
    await _answer_callback(callback_id, "Marqué comme ignoré")
    await _send_message(chat_id, f"🗑 *Email ignoré.*\n\n`{email_id}` → statut « Ignoré » dans votre Sheet.")


async def handle_callback_query(query: dict):
    """Point d'entrée des callbacks Telegram (boutons sous les notifications)."""
    data = query.get("data", "")
    parts = data.split(":")
    if len(parts) != 3:
        return

    action, email_id, draft_id = parts
    chat_id = query["message"]["chat"]["id"]
    callback_id = query["id"]

    if action == "send":
        await handle_envoyer(chat_id, email_id, draft_id, callback_id)
    elif action == "ignore":
        await handle_ignorer(chat_id, email_id, draft_id, callback_id)
