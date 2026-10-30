"""Configure le webhook Telegram avec son secret obligatoire."""

import json
import os
import sys
import urllib.request

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
BASE_URL = os.environ.get("BASE_URL", "").strip().rstrip("/")
SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
WEBHOOK_PATH = "/api/v1/telegram/webhook"

missing = [
    name
    for name, value in (
        ("TELEGRAM_BOT_TOKEN", TOKEN),
        ("BASE_URL", BASE_URL),
        ("TELEGRAM_WEBHOOK_SECRET", SECRET),
    )
    if not value
]
if missing:
    print(
        "Configuration Telegram incomplète : " + ", ".join(missing),
        file=sys.stderr,
    )
    raise SystemExit(1)

webhook_url = f"{BASE_URL}{WEBHOOK_PATH}"
payload = {
    "url": webhook_url,
    "secret_token": SECRET,
}
request = urllib.request.Request(
    f"https://api.telegram.org/bot{TOKEN}/setWebhook",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=20) as response:
    result = json.loads(response.read().decode("utf-8"))

if not result.get("ok"):
    print("Échec de configuration du webhook.", file=sys.stderr)
    raise SystemExit(1)
print("Configuration du webhook terminée.")
