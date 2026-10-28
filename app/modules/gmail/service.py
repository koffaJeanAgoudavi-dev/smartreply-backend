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
from datetime import datetime, timezone

import httpx

from app.core.config import settings

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Cache simple de l'access token (évite un refresh à chaque appel)
_token_cache: dict = {"access_token": None, "expires_at": 0.0}


async def _get_access_token_for_refresh_token(
    refresh_token: str,
    *,
    token_cache: dict | None = None,
    legacy_errors: bool = False,
) -> str:
    """Cœur OAuth paramétré commun aux comptes historique et d'entreprise."""
    if (
        token_cache is not None
        and token_cache["access_token"]
        and time.time() < token_cache["expires_at"]
    ):
        return token_cache["access_token"]

    payload = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(GOOGLE_TOKEN_URL, data=payload)

    if legacy_errors:
        if response.status_code != 200:
            raise RuntimeError(
                f"Refresh token échoué (HTTP {response.status_code}): {response.text}"
            )
        data = response.json()
        access_token = data["access_token"]
    else:
        data = _safe_response_json(response)
        if response.status_code == 400 and data.get("error") == "invalid_grant":
            raise GmailTokenRevokedError("gmail_token_revoked", status_code=400)
        if response.status_code != 200:
            _raise_company_gmail_error(response, "token")
        access_token = data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise CompanyGmailError("gmail_token_response_invalid")

    if token_cache is not None:
        token_cache["access_token"] = access_token
        token_cache["expires_at"] = time.time() + data.get("expires_in", 3600) - 60
    return access_token


async def _get_access_token() -> str:
    """Récupère le token historique en conservant son cache et ses erreurs."""
    return await _get_access_token_for_refresh_token(
        settings.GOOGLE_REFRESH_TOKEN,
        token_cache=_token_cache,
        legacy_errors=True,
    )


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


async def _list_unread_emails_with_access_token(
    access_token: str,
    *,
    max_results: int,
) -> list[dict]:
    """Cœur paramétré de la lecture historique des messages non lus."""
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
                "list_unsubscribe": headers_map.get("list-unsubscribe", ""),
                "snippet": msg.get("snippet", ""),
                "corps": _extract_text(msg["payload"]),
            })
        return emails


async def list_unread_emails(max_results: int = 5) -> list[dict]:
    """
    Module 1 — Détection : retourne les emails non lus du compte configuré.
    Chaque email : id_gmail, expediteur, objet, date, snippet, corps.
    """
    return await _list_unread_emails_with_access_token(
        await _get_access_token(),
        max_results=max_results,
    )


class CompanyGmailError(RuntimeError):
    """Erreur Gmail sûre pour le pipeline par entreprise."""

    def __init__(self, code: str, *, status_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class GmailTokenRevokedError(CompanyGmailError):
    """Le refresh token utilisateur n'est plus accepté par Google."""


class GmailQuotaError(CompanyGmailError):
    """Google demande d'arrêter temporairement les appels."""


class GmailDraftNotFoundError(CompanyGmailError):
    """Le brouillon ciblé n'existe plus dans le compte Gmail de l'entreprise."""


def _safe_response_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _raise_company_gmail_error(response: httpx.Response, operation: str) -> None:
    if response.status_code == 429:
        raise GmailQuotaError("gmail_quota_429", status_code=429)
    raise CompanyGmailError(
        f"gmail_{operation}_http_{response.status_code}",
        status_code=response.status_code,
    )


async def get_company_access_token(refresh_token: str) -> str:
    """Échange explicitement le refresh token d'une entreprise, sans cache global."""
    return await _get_access_token_for_refresh_token(refresh_token)


async def list_company_message_page(
    access_token: str,
    *,
    after_epoch_seconds: int,
    page_token: str | None = None,
    page_size: int = 100,
) -> tuple[tuple[str, ...], str | None]:
    """Liste une page d'IDs Gmail reçus après la coupure, sans lire leur contenu."""
    params: dict[str, str | int] = {
        "q": f"in:inbox after:{after_epoch_seconds}",
        "maxResults": max(1, min(500, page_size)),
    }
    if page_token:
        params["pageToken"] = page_token
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{GMAIL_API}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
    if response.status_code != 200:
        _raise_company_gmail_error(response, "list")
    data = _safe_response_json(response)
    messages = data.get("messages", [])
    if not isinstance(messages, list):
        raise CompanyGmailError("gmail_list_response_invalid")
    message_ids = tuple(
        message["id"]
        for message in messages
        if isinstance(message, dict) and isinstance(message.get("id"), str)
    )
    next_page_token = data.get("nextPageToken")
    if not isinstance(next_page_token, str):
        next_page_token = None
    return message_ids, next_page_token


async def get_company_email(access_token: str, gmail_message_id: str) -> dict:
    """Lit un message avec son internalDate et les données nécessaires au flux."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{GMAIL_API}/messages/{gmail_message_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"format": "full"},
        )
    if response.status_code != 200:
        _raise_company_gmail_error(response, "detail")
    message = _safe_response_json(response)
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise CompanyGmailError("gmail_message_payload_invalid")
    try:
        internal_date_ms = int(message["internalDate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CompanyGmailError("gmail_internal_date_invalid") from exc

    headers_map = {
        header["name"].lower(): header["value"]
        for header in payload.get("headers", [])
        if isinstance(header, dict)
        and isinstance(header.get("name"), str)
        and isinstance(header.get("value"), str)
    }
    return {
        "id_gmail": message.get("id", gmail_message_id),
        "thread_id": message.get("threadId"),
        "expediteur": headers_map.get("from", ""),
        "objet": headers_map.get("subject", "(sans objet)"),
        "message_id_header": headers_map.get("message-id", ""),
        "list_unsubscribe": headers_map.get("list-unsubscribe", ""),
        "snippet": message.get("snippet", ""),
        "corps": _extract_text(payload),
        "received_at": datetime.fromtimestamp(
            internal_date_ms / 1000,
            tz=timezone.utc,
        ),
    }


async def company_gmail_draft_exists(
    access_token: str,
    gmail_draft_id: str,
) -> bool:
    """Vérifie un brouillon sans le modifier; 404 signifie qu'il a été consommé."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{GMAIL_API}/drafts/{gmail_draft_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"format": "minimal"},
        )
    if response.status_code == 404:
        return False
    if response.status_code != 200:
        _raise_company_gmail_error(response, "draft_get")
    return True


async def send_company_gmail_draft(access_token: str, gmail_draft_id: str) -> None:
    """Envoie un brouillon avec le token explicite de son entreprise."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{GMAIL_API}/drafts/send",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"id": gmail_draft_id},
        )
    if response.status_code == 404:
        raise GmailDraftNotFoundError("gmail_draft_missing", status_code=404)
    if response.status_code != 200:
        _raise_company_gmail_error(response, "draft_send")
