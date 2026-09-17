"""
SmartReply Agent — Pointage du webhook Telegram vers Render
===========================================================
À lancer UNE FOIS après chaque redémarrage/ changement d'URL Render
(les URL gratuites de Render changent si le service est supprimé/recréé).

Lancement (en local ou via un job Render one-off) :
    python scripts/setup_webhook.py

Prérequis : variables d'environnement TELEGRAM_BOT_TOKEN, BASE_URL et
TELEGRAM_WEBHOOK_SECRET définies (mêmes valeurs que sur Render).
"""
import json
import os
import secrets
import urllib.request

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
WEBHOOK_PATH = "/api/v1/telegram/webhook"

# Si aucun secret n'est défini, on en génère un et on l'affiche
# pour que vous l'ajoutiez à Render (variable TELEGRAM_WEBHOOK_SECRET).
secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
if not secret:
    secret = secrets.token_urlsafe(32)
    print("⚠️  Aucun TELEGRAM_WEBHOOK_SECRET défini.")
    print(f"    Secret généré : {secret}")
    print("    → Ajoutez-le sur Render (Environment Variables) puis relancez ce script.")
    print()

webhook_url = f"{BASE_URL}{WEBHOOK_PATH}"
payload = {
    "url": webhook_url,
    "secret_token": secret,
}
req = urllib.request.Request(
    f"https://api.telegram.org/bot{TOKEN}/setWebhook",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=20) as resp:
    result = json.loads(resp.read().decode("utf-8"))

print("setWebhook :", result)
if result.get("ok"):
    print(f"✅ Webhook pointé vers {webhook_url}")
else:
    print("❌ Échec — vérifiez le token et l'URL.")
