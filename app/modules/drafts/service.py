"""Création des brouillons Gmail isolés par entreprise."""

import base64

import httpx

from app.modules.gmail.service import CompanyGmailError, GmailQuotaError


async def _create_gmail_draft_with_token(
    access_token: str,
    *,
    destinataire: str,
    objet: str,
    corps: str,
    thread_id: str,
    in_reply_to: str = "",
) -> str:
    """Crée un brouillon Gmail avec le jeton explicite de l'entreprise."""
    reply_headers = ""
    if in_reply_to:
        reply_headers = (
            f"In-Reply-To: {in_reply_to}\r\n"
            f"References: {in_reply_to}\r\n"
        )
    mime = (
        f"To: {destinataire}\r\n"
        f"Subject: {objet}\r\n"
        f"{reply_headers}"
        'Content-Type: text/plain; charset="UTF-8"\r\n'
        "\r\n"
        f"{corps}"
    )
    raw = base64.urlsafe_b64encode(mime.encode("utf-8")).decode("ascii")
    message = {"raw": raw, "threadId": thread_id}

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"message": message},
        )

    if response.status_code == 429:
        raise GmailQuotaError("gmail_draft_quota_429", status_code=429)
    if response.status_code != 200:
        raise CompanyGmailError(
            f"gmail_draft_http_{response.status_code}",
            status_code=response.status_code,
        )
    try:
        draft_id = response.json()["id"]
    except (KeyError, TypeError, ValueError) as exc:
        raise CompanyGmailError("gmail_draft_response_invalid") from exc
    if not isinstance(draft_id, str) or not draft_id:
        raise CompanyGmailError("gmail_draft_response_invalid")
    return draft_id


async def create_company_gmail_draft(
    access_token: str,
    *,
    destinataire: str,
    objet: str,
    corps: str,
    thread_id: str,
    in_reply_to: str = "",
) -> str:
    """Crée un brouillon dans le fil Gmail d'origine de l'entreprise."""
    return await _create_gmail_draft_with_token(
        access_token,
        destinataire=destinataire,
        objet=objet,
        corps=corps,
        thread_id=thread_id,
        in_reply_to=in_reply_to,
    )
