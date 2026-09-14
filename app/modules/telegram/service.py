import httpx
from app.core.config import settings

TELEGRAM_API_URL = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"

async def send_telegram_message(chat_id: int, text: str):
    """Envoie un message à un utilisateur Telegram via l'API Telegram Bot."""
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)

async def process_telegram_update(update: dict):
    """Traite l'événement entrant envoyé par le Webhook Telegram."""
    if "message" not in update:
        return

    message = update["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    # 1. Gestion des commandes de base
    if text.startswith("/start"):
        parts = text.split()
        if len(parts) > 1:
            code = parts[1]
            await handle_connection_code(chat_id, message, code)
        else:
            msg = (
                "👋 *Bienvenue sur SmartReply Agent !*\n\n"
                "Pour connecter votre compte Telegram à SmartReply, envoyez votre code de connexion (ex: `SR-82941`).\n\n"
                "💡 *Vous pouvez obtenir ce code depuis votre tableau de bord SmartReply.*"
            )
            await send_telegram_message(chat_id, msg)

    elif text == "/status":
        msg = (
            "📊 *Statut de votre compte SmartReply*\n\n"
            "• **Statut :** ⚪ Non connecté\n\n"
            "Envoyez votre code `SR-XXXXX` pour vous associer."
        )
        await send_telegram_message(chat_id, msg)

    elif text == "/help":
        msg = (
            "❓ *Aide - SmartReply Agent*\n\n"
            "• `/start` — Démarrer ou vérifier la connexion\n"
            "• `/connect` — Associer votre compte avec un code\n"
            "• `/status` — Vérifier l'état de votre connexion\n"
            "• `/help` — Afficher ce message d'aide"
        )
        await send_telegram_message(chat_id, msg)

    elif text.startswith("SR-"):
        await handle_connection_code(chat_id, message, text)

    else:
        msg = "🤖 Commande non reconnue. Envoyez votre code de connexion (ex: `SR-82941`) ou utilisez `/help`."
        await send_telegram_message(chat_id, msg)

async def handle_connection_code(chat_id: int, message: dict, code: str):
    """Traite la tentative de connexion avec le code SR-XXXXX."""
    # TODO: Connecter avec Google Sheets dans l'étape suivante pour valider le code
    msg = f"⏳ Traitement du code `{code}` en cours..."
    await send_telegram_message(chat_id, msg)
