"""Stockage dans le Google Sheet propre à chaque entreprise."""

from app.db.sheets_client import get_sheets_client


def _get_workbook_by_id(sheet_id: str, *, require_nonempty_id: bool):
    """Ouvre un classeur explicite, avec validation activable par l'appelant."""
    if require_nonempty_id and (not sheet_id or not sheet_id.strip()):
        raise ValueError("company_sheet_id_missing")
    return get_sheets_client().open_by_key(sheet_id)


EMAIL_HEADERS = [
    "Email_ID",
    "Expéditeur",
    "Objet",
    "Résumé",
    "Catégorie",
    "Priorité",
    "Statut",
    "Brouillon_ID",
]
DRAFT_HEADERS = ["Brouillon_ID", "Email_ID", "Contenu", "Statut"]
EMAIL_STATUSES = {"Nouveau", "Analysé", "Brouillon prêt", "Envoyé", "Ignoré"}
DRAFT_STATUSES = {"En attente", "Envoyé", "Ignoré"}


def _rows_with_fixed_headers(sheet, expected_headers: list[str]) -> list[list[str]]:
    rows = sheet.get_all_values()
    if not rows:
        sheet.append_row(expected_headers)
        return [list(expected_headers)]
    if rows[0][: len(expected_headers)] != expected_headers:
        raise RuntimeError("company_sheet_headers_invalid")
    return rows


def get_company_workbook(sheet_id: str):
    """Ouvre explicitement le Sheet d'une entreprise, sans variable globale."""
    return _get_workbook_by_id(sheet_id, require_nonempty_id=True)


def list_company_email_rows(workbook) -> list[dict[str, str]]:
    """Lit les colonnes publiques d'Emails, sans contenu ni identifiant draft."""
    worksheet = workbook.worksheet("Emails")
    identity_rows = worksheet.get("A:C")
    analytics_rows = worksheet.get("E:G")
    if not identity_rows and not analytics_rows:
        return []
    if (
        not identity_rows
        or identity_rows[0][:3] != EMAIL_HEADERS[:3]
        or not analytics_rows
        or analytics_rows[0][:3] != EMAIL_HEADERS[4:7]
    ):
        raise RuntimeError("company_sheet_headers_invalid")

    result = []
    row_count = max(len(identity_rows), len(analytics_rows))
    for row_index in range(1, row_count):
        identity = (
            list(identity_rows[row_index][:3])
            if row_index < len(identity_rows)
            else []
        )
        analytics = (
            list(analytics_rows[row_index][:3])
            if row_index < len(analytics_rows)
            else []
        )
        identity.extend([""] * (3 - len(identity)))
        analytics.extend([""] * (3 - len(analytics)))
        result.append(
            {
                "email_id": str(identity[0]),
                "sender": str(identity[1]),
                "subject": str(identity[2]),
                "category": str(analytics[0]),
                "priority": str(analytics[1]),
                "status": str(analytics[2]),
            }
        )
    return result


def _row_with_value(rows: list[list[str]], column: int, value: str) -> int | None:
    for row_index, row in enumerate(rows[1:], start=2):
        if len(row) > column and str(row[column]) == value:
            return row_index
    return None


def upsert_company_email(
    workbook,
    *,
    email_id: str,
    expediteur: str,
    objet: str,
    resume: str,
    categorie: str,
    priorite: str,
    statut: str,
    brouillon_id: str = "",
) -> int:
    """Insère ou remplace la ligne Emails identifiée, dans un seul classeur."""
    if statut not in EMAIL_STATUSES:
        raise ValueError("company_email_status_invalid")
    sheet = workbook.worksheet("Emails")
    rows = _rows_with_fixed_headers(sheet, EMAIL_HEADERS)
    values = [
        email_id,
        expediteur,
        objet,
        resume,
        categorie,
        priorite,
        statut,
        brouillon_id,
    ]
    row_index = _row_with_value(rows, 0, email_id)
    if row_index is None:
        sheet.append_row(values)
        return len(rows) + 1
    sheet.update(range_name=f"A{row_index}:H{row_index}", values=[values])
    return row_index


def get_company_draft_id(workbook, *, email_id: str) -> str | None:
    """Retourne le brouillon déjà consigné pour cet Email_ID, le cas échéant."""
    sheet = workbook.worksheet("Brouillons")
    rows = _rows_with_fixed_headers(sheet, DRAFT_HEADERS)
    row_index = _row_with_value(rows, 1, email_id)
    if row_index is None:
        return None
    row = rows[row_index - 1]
    return str(row[0]) if row and row[0] else None


def upsert_company_draft(
    workbook,
    *,
    draft_id: str,
    email_id: str,
    contenu: str,
    statut: str = "En attente",
) -> int:
    """Conserve une seule ligne Brouillons par Email_ID dans l'entreprise."""
    if statut not in DRAFT_STATUSES:
        raise ValueError("company_draft_status_invalid")
    sheet = workbook.worksheet("Brouillons")
    rows = _rows_with_fixed_headers(sheet, DRAFT_HEADERS)
    values = [draft_id, email_id, contenu, statut]
    row_index = _row_with_value(rows, 1, email_id)
    if row_index is None:
        sheet.append_row(values)
        return len(rows) + 1
    sheet.update(range_name=f"A{row_index}:D{row_index}", values=[values])
    return row_index


def mark_company_email_draft_ready(
    workbook,
    *,
    email_id: str,
    draft_id: str,
) -> None:
    """Effectue la deuxième écriture : statut et Brouillon_ID dans Emails."""
    sheet = workbook.worksheet("Emails")
    rows = _rows_with_fixed_headers(sheet, EMAIL_HEADERS)
    row_index = _row_with_value(rows, 0, email_id)
    if row_index is None:
        raise RuntimeError("company_email_row_missing")
    sheet.update_cell(row_index, 7, "Brouillon prêt")
    sheet.update_cell(row_index, 8, draft_id)


def get_company_notification_data(workbook, *, email_id: str) -> dict | None:
    """Relit le résumé déjà produit sans rappeler Gmail ni Gemini."""
    sheet = workbook.worksheet("Emails")
    rows = _rows_with_fixed_headers(sheet, EMAIL_HEADERS)
    row_index = _row_with_value(rows, 0, email_id)
    if row_index is None:
        return None
    row = rows[row_index - 1]
    if len(row) < len(EMAIL_HEADERS):
        return None
    return {
        "expediteur": str(row[1]),
        "objet": str(row[2]),
        "resume": str(row[3]),
        "categorie": str(row[4]),
        "priorite": str(row[5]),
    }


def get_company_draft_content(
    workbook,
    *,
    email_id: str,
    draft_id: str,
) -> str | None:
    """Retourne le contenu seulement si les deux identifiants concordent."""
    sheet = workbook.worksheet("Brouillons")
    rows = _rows_with_fixed_headers(sheet, DRAFT_HEADERS)
    row_index = _row_with_value(rows, 1, email_id)
    if row_index is None:
        return None
    row = rows[row_index - 1]
    if len(row) < len(DRAFT_HEADERS) or str(row[0]) != draft_id:
        return None
    return str(row[2])


def update_company_draft_status(
    workbook,
    *,
    email_id: str,
    draft_id: str,
    statut: str,
) -> None:
    """Met à jour les deux onglets d'un Sheet d'entreprise de façon idempotente."""
    if statut not in {"Envoyé", "Ignoré"}:
        raise ValueError("company_action_status_invalid")

    email_sheet = workbook.worksheet("Emails")
    email_rows = _rows_with_fixed_headers(email_sheet, EMAIL_HEADERS)
    email_row = _row_with_value(email_rows, 0, email_id)

    draft_sheet = workbook.worksheet("Brouillons")
    draft_rows = _rows_with_fixed_headers(draft_sheet, DRAFT_HEADERS)
    draft_row = _row_with_value(draft_rows, 1, email_id)
    if email_row is None or draft_row is None:
        raise RuntimeError("company_action_sheet_row_missing")
    if str(draft_rows[draft_row - 1][0]) != draft_id:
        raise RuntimeError("company_action_sheet_draft_mismatch")

    email_sheet.update_cell(email_row, 7, statut)
    draft_sheet.update_cell(draft_row, 4, statut)
