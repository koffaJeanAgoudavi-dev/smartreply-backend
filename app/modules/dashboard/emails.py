"""Lecture paginée et cloisonnée des emails enregistrés dans le système."""

import re
import unicodedata
from email.utils import parseaddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.companies.models import Company
from app.modules.dashboard.schemas import (
    DashboardEmailItem,
    DashboardEmailPagination,
    DashboardEmailsResponse,
)
from app.modules.dashboard.service import (
    CATEGORY_LABELS,
    PRIORITY_LABELS,
    CompanySheetReadError,
    _as_utc,
    _limited_text,
    _safe_label,
    _safe_status_label,
)
from app.modules.processed_emails.models import ProcessedEmail
from app.modules.sheets.service import (
    get_company_workbook,
    list_company_email_rows_with_summary,
)

_EMAIL_ID = re.compile(r"^EM-([1-9][0-9]*)$")
_ELIGIBLE_RECORD_STATUSES = ("done", "skipped")


def _normalize(value: object) -> str:
    """Normalise une valeur pour une recherche insensible à la casse et aux accents."""
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    without_accents = "".join(
        char for char in text if not unicodedata.combining(char)
    )
    return " ".join(without_accents.split())


def _sender_parts(raw: object) -> tuple[str, str]:
    """Sépare le nom d'affichage et l'adresse sans renvoyer le header Gmail brut."""
    name, address = parseaddr(str(raw or "").strip())
    address = address.strip()[:320]
    name = name.strip()
    if not name and address:
        name = address.split("@", 1)[0]
    if not name:
        name = "Expéditeur"
    return name[:160], address


def _read_rows(company: Company) -> dict[int, dict[str, str]]:
    """Indexe les lignes du Sheet courant; la dernière occurrence d'un ID prévaut."""
    try:
        workbook = get_company_workbook(company.sheet_id)
        rows = list_company_email_rows_with_summary(workbook)
    except Exception as exc:
        raise CompanySheetReadError("company_sheet_unavailable") from exc

    indexed: dict[int, dict[str, str]] = {}
    for row in rows:
        match = _EMAIL_ID.fullmatch(str(row.get("email_id", "")))
        if match:
            indexed[int(match.group(1))] = row
    return indexed


def _records(db: Session, company: Company, ids: set[int]):
    """Charge en une requête les seuls traitements finaux du tenant authentifié."""
    if not ids:
        return []
    return db.execute(
        select(
            ProcessedEmail.id,
            ProcessedEmail.received_at,
            ProcessedEmail.category,
            ProcessedEmail.priority,
            ProcessedEmail.status,
            ProcessedEmail.draft_status,
        ).where(
            ProcessedEmail.company_id == company.id,
            ProcessedEmail.id.in_(ids),
            ProcessedEmail.status.in_(_ELIGIBLE_RECORD_STATUSES),
        )
    ).all()


def _item(record, row: dict[str, str]) -> DashboardEmailItem:
    name, address = _sender_parts(row.get("sender", ""))
    return DashboardEmailItem(
        email_id=f"EM-{record.id}",
        sender_name=name,
        sender_email=address,
        subject=_limited_text(row.get("subject", ""), 500),
        received_at=_as_utc(record.received_at) if record.received_at else None,
        summary=str(row.get("summary", "") or "").strip(),
        category=_safe_label(
            row.get("category", ""),
            labels=CATEGORY_LABELS,
            database_value=record.category,
        ),
        priority=_safe_label(
            row.get("priority", ""),
            labels=PRIORITY_LABELS,
            database_value=record.priority,
        ),
        status=_safe_status_label(
            row.get("status", ""),
            record_status=record.status,
            draft_status=record.draft_status,
        ),
    )


def _filtered_items(
    db: Session,
    company: Company,
    *,
    search: str | None = None,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
) -> list[DashboardEmailItem]:
    rows = _read_rows(company)
    records = _records(db, company, set(rows))
    items = [_item(record, rows[int(record.id)]) for record in records]

    needle = _normalize(search)
    category_key, priority_key, status_key = map(
        _normalize,
        (category, priority, status),
    )
    if needle:
        items = [
            item
            for item in items
            if needle
            in _normalize(
                " ".join((item.sender_name, item.sender_email, item.subject))
            )
        ]
    if category_key:
        items = [
            item for item in items if _normalize(item.category) == category_key
        ]
    if priority_key:
        items = [
            item for item in items if _normalize(item.priority) == priority_key
        ]
    if status_key:
        items = [item for item in items if _normalize(item.status) == status_key]

    items.sort(
        key=lambda item: (
            item.received_at.timestamp()
            if item.received_at
            else float("-inf"),
            int(item.email_id[3:]),
        ),
        reverse=True,
    )
    return items


def list_company_emails(
    db: Session,
    *,
    company: Company,
    page: int,
    page_size: int,
    search: str | None = None,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
) -> DashboardEmailsResponse:
    """Filtre, trie puis pagine les emails enregistrés pour une entreprise."""
    items = _filtered_items(
        db,
        company,
        search=search,
        category=category,
        priority=priority,
        status=status,
    )
    total = len(items)
    total_pages = (total + page_size - 1) // page_size
    start = (page - 1) * page_size
    return DashboardEmailsResponse(
        items=items[start : start + page_size],
        pagination=DashboardEmailPagination(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
        ),
    )


def get_company_email(
    db: Session,
    *,
    company: Company,
    email_id: str,
) -> DashboardEmailItem | None:
    """Retourne un email final du tenant, ou None sans révéler la cause du refus."""
    match = _EMAIL_ID.fullmatch(email_id or "")
    if match is None:
        return None

    record_id = int(match.group(1))
    row = _read_rows(company).get(record_id)
    if row is None:
        return None

    records = _records(db, company, {record_id})
    if not records:
        return None
    return _item(records[0], row)
