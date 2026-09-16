import httpx
from datetime import datetime, timezone
from app.core.config import settings
from app.db.sheets_client import get_central_workbook

TELEGRAM_API_URL = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"

async def send_telegram_message(chat_id: int, text: str):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)

async def process_telegram_update(update: dict):
    if "message" not in update:
        return

    message = update["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if text.startswith("/start"):
        parts = text.split()
        if len(parts) > 1:
            await handle_connection_code(chat_id, message, parts[1])
        else:
            msg = (
                "👋 *Bienvenue sur SmartReply Agent !*\n\n"
                "Pour connecter votre compte Telegram, envoyez votre code de connexion (ex: `SR-82941`).\n\n"
                "💡 *Obtenez votre code depuis le tableau de bord SmartReply.*"
            )
            await send_telegram_message(chat_id, msg)

    elif text == "/status":
        await check_user_status(chat_id)

    elif text == "/help":
        msg = (
            "❓ *Aide - SmartReply Agent*\n\n"
            "• `/start` — Démarrer le bot\n"
            "• `/status` — Vérifier votre état de connexion\n"
            "• `/help` — Afficher l'aide"
        )
        await send_telegram_message(chat_id, msg)

    elif text.startswith("SR-"):
        await handle_connection_code(chat_id, message, text)

    else:
        msg = "🤖 Commande non reconnue. Envoyez un code valide (ex: `SR-82941`) ou `/help`."
        await send_telegram_message(chat_id, msg)

async def handle_connection_code(chat_id: int, message: dict, code: str):
    """Vérifie et valide le code de connexion dans Google Sheets."""
    try:
        wb = get_central_workbook()
        sheet = wb.worksheet("telegram_connection_codes")
        records = sheet.get_all_records()

        # Recherche du code
        row_idx = None
        target_record = None
        for idx, record in enumerate(records, start=2):  # Les données commencent à la ligne 2
            if record.get("connection_code") == code:
                row_idx = idx
                target_record = record
                break

        if not target_record:
            await send_telegram_message(chat_id, "❌ *Code invalide.*\nVérifiez votre code et réessayez.")
            return

        status = target_record.get("status")
        expires_at_str = target_record.get("expires_at")

        if status == "used":
            await send_telegram_message(chat_id, "⚠️ *Code déjà utilisé.*\nVeuillez générer un nouveau code depuis SmartReply.")
            return

        if status in ["expired", "cancelled"]:
            await send_telegram_message(chat_id, "⏳ *Code expiré.*\nRetournez dans SmartReply pour générer un nouveau code.")
            return

        # Mise à jour des informations de connexion
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        telegram_user_id = message["from"]["id"]
        
        # Mettre à jour la ligne dans Google Sheets (status, used_at, telegram_user_id, telegram_chat_id)
        sheet.update_cell(row_idx, 4, "used")               # Col 4 : status
        sheet.update_cell(row_idx, 7, now_str)              # Col 7 : used_at
        sheet.update_cell(row_idx, 8, str(telegram_user_id))# Col 8 : telegram_user_id
        sheet.update_cell(row_idx, 9, str(chat_id))         # Col 9 : telegram_chat_id

        await send_telegram_message(
            chat_id,
            "🎉 *Connexion réussie !*\n\nVotre compte Telegram est maintenant connecté à SmartReply Agent.\n"
            "Vous recevrez ici les notifications de vos emails professionnels."
        )

    except Exception as e:
        await send_telegram_message(chat_id, "⚠️ Une erreur est survenue lors de la vérification du code.")

async def check_user_status(chat_id: int):
    """Vérifie si le chat_id est présent dans la base."""
    try:
        wb = get_central_workbook()
        sheet = wb.worksheet("telegram_connection_codes")
        records = sheet.get_all_records()

        connected = any(str(r.get("telegram_chat_id")) == str(chat_id) and r.get("status") == "used" for r in records)

        if connected:
            await send_telegram_message(chat_id, "📊 *Statut :* 🟢 Connecté à SmartReply Agent")
        else:
            await send_telegram_message(chat_id, "📊 *Statut :* ⚪ Non connecté. Envoyez votre code `SR-XXXXX`.")
    except Exception:
        await send_telegram_message(chat_id, "📊 *Statut :* Impossible de récupérer le statut pour le moment.")
        
