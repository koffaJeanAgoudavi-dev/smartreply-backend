"""
SmartReply Agent — MODULE 4 : Google Sheets (Stockage)
======================================================
Écriture des emails analysés dans le Google Sheet Data Entreprise
(onglet "Emails", §8.3 du cadrage).

En-têtes figés : Email_ID, Expéditeur, Objet, Résumé, Catégorie,
Priorité, Statut, Brouillon_ID
"""
from datetime import datetime, timezone
from app.core.config import settings
from app.db.sheets_client import get_sheets_client


def get_data_workbook():
    """Ouvre le classeur Data Entreprise (onglets Emails, Brouillons, Clients…)."""
    client = get_sheets_client()
    return client.open_by_key(settings.GOOGLE_DATA_SHEET_ID)


def append_analyzed_email(
    expediteur: str,
    objet: str,
    resume: str,
    categorie: str,
    priorite: str,
    statut: str = "Analysé",
    brouillon_id: str = "",
    email_id: str = "",
) -> dict:
    """
    Ajoute une ligne dans l'onglet "Emails" du Sheet Data Entreprise.
    Retourne {"email_id", "ligne"}.
    """
    wb = get_data_workbook()
    sheet = wb.worksheet("Emails")

    if not email_id:
        email_id = "EM-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    sheet.append_row([
        email_id,
        expediteur,
        objet,
        resume,
        categorie,
        priorite,
        statut,
        brouillon_id,
    ])
    return {"email_id": email_id, "ligne": len(sheet.get_all_values())}
