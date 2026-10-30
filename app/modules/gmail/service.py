"""Adaptateurs Gmail isolés par entreprise."""

import base64
import re
from datetime import datetime, timezone

import httpx

from app.core.config import settings

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

async def _get_access_token_for_refresh_token(refresh_token: str) -> str:
    """Échange le refresh token explicite d'une entreprise."""
    payload = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(GOOGLE_TOKEN_URL, data=payload)

    data = _safe_response_json(response)
    if response.status_code == 400 and data.get("error") == "invalid_grant":
        raise GmailTokenRevokedError("gmail_token_revoked", status_code=400)
    if response.status_code != 200:
        _raise_company_gmail_error(response, "token")
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise CompanyGmailError("gmail_token_response_invalid")
    return access_token


def _strip_html(html: str) -> str:
    """Supprime les balises HTML et compresse les espaces (texte lisible)."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _decode_body_data(data: str) -> str:
    return base64.urlsafe_b64decode(data).decode(
        "utf-8",
        errors="replace",
    )


def _extract_text(payload: dict) -> str:
    """Extrait le texte brut, avec HTML nettoyé en dernier recours."""
    if payload.get("mimeType", "").startswith("multipart"):
        for part in payload.get("parts", []):
            body_data = part.get("body", {}).get("data")
            if part.get("mimeType") == "text/plain" and body_data:
                return _decode_body_data(body_data).strip()
        for part in payload.get("parts", []):
            body_data = part.get("body", {}).get("data")
            if part.get("mimeType") == "text/html" and body_data:
                return _strip_html(_decode_body_data(body_data))
    elif payload.get("body", {}).get("data"):
        raw = _decode_body_data(payload["body"]["data"])
        if payload.get("mimeType") == "text/html":
            return _strip_html(raw)
        return raw.strip()
    return ""


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
