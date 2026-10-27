"""
SmartReply Agent — MODULE 5 : Génération du brouillon Gmail
============================================================
Crée un brouillon dans le compte Gmail configuré via l'API Gmail,
et consigne la ligne dans l'onglet "Brouillons" du Sheet Data Entreprise
(mécanisme de double écriture repris de V1.5).

En-têtes onglet Brouillons : Brouillon_ID, Email_ID, Contenu, Statut
"""
import base64

import httpx

from app.modules.gmail.service import _get_access_token
from app.modules.sheets.service import get_data_workbook


async def create_gmail_draft(destinataire: str, objet: str, corps: str) -> str:
    """Crée un brouillon Gmail et retourne son ID."""
    access_token = await _get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    mime = (
        f"To: {destinataire}\r\n"
        f"Subject: {objet}\r\n"
        f"Content-Type: text/plain; charset=\"UTF-8\"\r\n"
        f"\r\n"
        f"{corps}"
    )
    raw = base64.urlsafe_b64encode(mime.encode("utf-8")).decode("ascii")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            headers=headers,
            json={"message": {"raw": raw}},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Création brouillon échouée (HTTP {resp.status_code}): {resp.text}")
        return resp.json()["id"]


def record_draft_in_sheet(brouillon_id: str, email_id: str, contenu: str) -> int:
    """Ajoute la ligne dans l'onglet Brouillons (Statut : En attente)."""
    wb = get_data_workbook()
    sheet = wb.worksheet("Brouillons")
    sheet.append_row([brouillon_id, email_id, contenu, "En attente"])
    return len(sheet.get_all_values())


async def create_company_gmail_draft(
    access_token: str,
    *,
    destinataire: str,
    objet: str,
    corps: str,
    thread_id: str,
    in_reply_to: str = "",
) -> str:
    """Crée un brouillon avec un token explicite, dans le fil Gmail d'origine."""
    from app.modules.gmail.service import CompanyGmailError, GmailQuotaError

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
        f"Content-Type: text/plain; charset=\"UTF-8\"\r\n"
        f"\r\n"
        f"{corps}"
    )
    raw = base64.urlsafe_b64encode(mime.encode("utf-8")).decode("ascii")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"message": {"raw": raw, "threadId": thread_id}},
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
