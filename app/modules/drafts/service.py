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
from app.modules.sheets.service import append_draft_to_workbook, get_data_workbook


async def _create_gmail_draft_with_token(
    access_token: str,
    *,
    destinataire: str,
    objet: str,
    corps: str,
    thread_id: str | None,
    in_reply_to: str = "",
    legacy_errors: bool = False,
) -> str:
    """Cœur paramétré commun aux flux historique et par entreprise."""
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
    message = {"raw": raw}
    if thread_id is not None:
        message["threadId"] = thread_id

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"message": message},
        )

    if legacy_errors:
        if response.status_code != 200:
            raise RuntimeError(
                "Création brouillon échouée "
                f"(HTTP {response.status_code}): {response.text}"
            )
        return response.json()["id"]

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


async def create_gmail_draft(destinataire: str, objet: str, corps: str) -> str:
    """Crée un brouillon Gmail via le compte historique configuré."""
    return await _create_gmail_draft_with_token(
        await _get_access_token(),
        destinataire=destinataire,
        objet=objet,
        corps=corps,
        thread_id=None,
        legacy_errors=True,
    )


def record_draft_in_sheet(brouillon_id: str, email_id: str, contenu: str) -> int:
    """Ajoute la ligne dans l'onglet Brouillons (Statut : En attente)."""
    return append_draft_to_workbook(
        get_data_workbook(),
        brouillon_id=brouillon_id,
        email_id=email_id,
        contenu=contenu,
    )


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
    return await _create_gmail_draft_with_token(
        access_token,
        destinataire=destinataire,
        objet=objet,
        corps=corps,
        thread_id=thread_id,
        in_reply_to=in_reply_to,
    )
