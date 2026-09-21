"""
SmartReply Agent — PIPELINE COMPLET (Modules 1→6 chaînés)
==========================================================
Reproduit fidèlement le flux validé en V1.5 :
  Gmail → filtrage → analyse Gemini → Sheet Emails → brouillon Gmail
  → Sheet Brouillons + maj Statut → notification Telegram (3 boutons)

Garde-fou IA : si Gemini classe l'email en newsletter/spam malgré le
filtre, il est ignoré après analyse (statut « Ignoré », pas de brouillon
ni de notification).

Étape 1 : compte unique en dur (chat_id fixe).
"""
import re
import traceback
import httpx
from app.modules.gmail.service import list_unread_emails, _get_access_token
from app.modules.filtering.service import should_process
from app.modules.ai.service import analyze_email
from app.modules.sheets.service import append_analyzed_email, get_data_workbook
from app.modules.drafts.service import create_gmail_draft, record_draft_in_sheet
from app.modules.notifications.service import send_email_notification

CHAT_ID = 6599553668  # Étape 1 : compte unique en dur
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _extract_address(expediteur: str) -> str:
    """'Marie Dupont <marie@x.fr>' -> 'marie@x.fr'"""
    match = re.search(r"<([^>]+)>", expediteur or "")
    return match.group(1) if match else (expediteur or "")


def _set_draft_ready(email_id: str, draft_id: str):
    """Emails.Statut = 'Brouillon prêt' + Brouillon_ID (double écriture §8.3)."""
    wb = get_data_workbook()
    sheet = wb.worksheet("Emails")
    for idx, rec in enumerate(sheet.get_all_records(), start=2):
        if str(rec.get("Email_ID")) == str(email_id):
            sheet.update_cell(idx, 7, "Brouillon prêt")
            sheet.update_cell(idx, 8, draft_id)
            break


async def _mark_as_read(id_gmail: str):
    """Retire le label UNREAD pour ne pas retraiter l'email."""
    access_token = await _get_access_token()
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(
            f"{GMAIL_API}/messages/{id_gmail}/modify",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"removeLabelIds": ["UNREAD"]},
        )


async def process_one(email: dict) -> dict:
    """Traite un email unique à travers les modules 2→6."""
    result = {"id_gmail": email["id_gmail"], "objet": email["objet"]}

    # MODULE 2 — Filtrage (en-tête List-Unsubscribe inclus)
    decision = should_process(
        email["expediteur"],
        email["corps"] or email["snippet"],
        email.get("list_unsubscribe", ""),
    )
    result["filtrage"] = decision
    if decision["decision"] == "ignorer":
        result["statut_final"] = "ignoré (filtré)"
        return result

    # MODULE 3 — Analyse IA
    analyse = await analyze_email(email["expediteur"], email["objet"], email["corps"][:4000])
    result["analyse"] = analyse

    # GARDE-FOU IA : newsletter/spam détecté par Gemini -> ignoré
    if analyse.get("categorie") in ("newsletter", "spam"):
        append_analyzed_email(
            expediteur=email["expediteur"],
            objet=email["objet"],
            resume=analyse.get("resume", ""),
            categorie=analyse.get("categorie", "autre"),
            priorite=analyse.get("urgence", "moyenne"),
            statut="Ignoré",
        )
        await _mark_as_read(email["id_gmail"])
        result["statut_final"] = f"ignoré ({analyse.get('categorie')} confirmé par l'IA)"
        return result

    # MODULE 4 — Stockage (Statut : Analysé)
    stored = append_analyzed_email(
        expediteur=email["expediteur"],
        objet=email["objet"],
        resume=analyse.get("resume", ""),
        categorie=analyse.get("categorie", "autre"),
        priorite=analyse.get("urgence", "moyenne"),
        statut="Analysé",
    )
    email_id = stored["email_id"]
    result["email_id"] = email_id

    # MODULE 5 — Brouillon Gmail + onglet Brouillons
    draft_id = await create_gmail_draft(
        _extract_address(email["expediteur"]),
        f"Re: {email['objet']}",
        analyse.get("brouillon", ""),
    )
    record_draft_in_sheet(draft_id, email_id, analyse.get("brouillon", ""))
    result["brouillon_id"] = draft_id

    # Mise à jour Emails : Brouillon prêt + Brouillon_ID
    _set_draft_ready(email_id, draft_id)

    # MODULE 6 — Notification Telegram (3 boutons fonctionnels)
    await send_email_notification(
        chat_id=CHAT_ID,
        expediteur=email["expediteur"],
        objet=email["objet"],
        resume=analyse.get("resume", ""),
        categorie=analyse.get("categorie", "autre"),
        priorite=analyse.get("urgence", "moyenne"),
        email_id=email_id,
        draft_id=draft_id,
    )
    result["statut_final"] = "notifié ✅"

    # Marquer comme lu pour ne pas retraiter
    await _mark_as_read(email["id_gmail"])
    return result


async def run_pipeline() -> dict:
    """Lance le pipeline complet sur les emails non lus."""
    emails = await list_unread_emails(max_results=10)
    resultats = []
    for email in emails:
        try:
            resultats.append(await process_one(email))
        except Exception:
            traceback.print_exc()
            resultats.append({
                "id_gmail": email["id_gmail"],
                "objet": email["objet"],
                "statut_final": "❌ erreur — voir logs Render",
            })
    return {"emails_traites": len(resultats), "resultats": resultats}
