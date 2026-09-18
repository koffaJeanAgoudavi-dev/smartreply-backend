"""
SmartReply Agent — MODULE 1 : Réception des emails (Gmail API)
==============================================================
Connexion au compte Gmail configuré (Étape 1 : compte unique en variables
d'environnement) et lecture des messages.

Authentification : OAuth2 refresh token -> access token (flux standard Google).
Aucune librairie Google nécessaire : httpx uniquement (déjà dans requirements).
"""
import base64
import re
import time
import httpx
from app.core.config import settings

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Cache simple de l'access token (évite un refresh à chaque appel)
_token_cache: dict = {"access_token": None, "expires_at": 0.0}


async def _get_access_token() -> str:
    """Récupère un access token valide (le rafraîchit si expiré)."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    payload = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "refresh_token": settings.GOOGLE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Refresh token échoué (HTTP {resp.status_code}): {resp.text}")
        data = resp.json()

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600) - 60
    return _token_cache["access_token"]


def _strip_html(html: str) -> str:
    """Supprime les balises HTML et compresse les espaces (texte lisible)."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_text(payload: dict) -> str:
    """Extrait le texte brut d'un message (text/plain prioritaire, HTML nettoyé en secours)."""
    if payload.get("mimeType", "").startswith("multipart"):
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace").strip()
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                html = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                return _strip_html(html)
    elif payload.get("body", {}).get("data"):
        raw = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        if payload.get("mimeType") == "text/html":
            return _strip_html(raw)
        return raw.strip()
    return ""


async def list_unread_emails(max_results: int = 5) -> list[dict]:
    """
    Module 1 — Détection : retourne les emails non lus du compte configuré.
    Chaque email : id_gmail, expediteur, objet, date, snippet, corps.
    """
    access_token = await _get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=30) as client:
        # 1. Liste des messages non lus
        resp = await client.get(
            f"{GMAIL_API}/messages",
            headers=headers,
            params={"q": "is:unread", "maxResults": max_results},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Gmail list échoué (HTTP {resp.status_code}): {resp.text}")
        messages = resp.json().get("messages", [])

        # 2. Détail de chaque message
        emails = []
        for m in messages:
            detail = await client.get(
                f"{GMAIL_API}/messages/{m['id']}",
                headers=headers,
                params={"format": "full"},
            )
            if detail.status_code != 200:
                continue
            msg = detail.json()

            headers_map = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
            emails.append({
                "id_gmail": msg["id"],
                "thread_id": msg.get("threadId"),
                "expediteur": headers_map.get("from", ""),
                "objet": headers_map.get("subject", "(sans objet)"),
                "date": headers_map.get("date", ""),
                "snippet": msg.get("snippet", ""),
                "corps": _extract_text(msg["payload"]),
            })
        return emails
