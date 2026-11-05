"""Consultation et contrôle cloisonnés des brouillons du dashboard."""

import logging
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.companies.models import Company
from app.modules.dashboard.emails import _sender_parts
from app.modules.dashboard.models import CompanyActivityEvent
from app.modules.dashboard.schemas import (
    DashboardDraftDetail,
    DashboardDraftIgnoreResponse,
    DashboardDraftItem,
    DashboardDraftMetrics,
    DashboardDraftPagination,
    DashboardDraftsResponse,
)
from app.modules.dashboard.service import (
    CATEGORY_LABELS,
    PRIORITY_LABELS,
    CompanySheetReadError,
    _as_utc,
    _limited_text,
    _safe_label,
)
from app.modules.processed_emails.models import ProcessedEmail
from app.modules.processed_emails.service import (
    claim_draft_discard,
    mark_draft_sheet_synced,
)
from app.modules.sheets.service import (
    get_company_workbook,
    list_company_draft_rows,
    list_company_email_rows_with_summary,
    update_company_draft_status,
)

logger = logging.getLogger(__name__)

_DRAFT_REF = re.compile(r"^BR-([1-9][0-9]*)$")
_DRAFT_STATUS_LABELS = {
    "pending": "En attente",
    "sent": "Envoyé",
    "discarded": "Ignoré",
    "sending": "En erreur",
    "missing": "En erreur",
}


class DraftActionConflictError(RuntimeError):
    """Le brouillon existe, mais son état ne permet plus l'action demandée."""


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join(
        "".join(char for char in text if not unicodedata.combining(char)).split()
    )


def _record_id(draft_ref: str) -> int | None:
    match = _DRAFT_REF.fullmatch(draft_ref or "")
    return int(match.group(1)) if match else None


def _read_sheet_rows(
    company: Company,
) -> tuple[dict[int, dict[str, str]], dict[int, dict[str, str]]]:
    """Indexe les deux onglets; une ligne sans identifiant sûr est ignorée."""
    try:
        workbook = get_company_workbook(company.sheet_id)
        email_rows = list_company_email_rows_with_summary(workbook)
        draft_rows = list_company_draft_rows(workbook)
    except Exception as exc:
        raise CompanySheetReadError("company_sheet_unavailable") from exc

    emails: dict[int, dict[str, str]] = {}
    drafts: dict[int, dict[str, str]] = {}
    for row in email_rows:
        match = re.fullmatch(r"EM-([1-9][0-9]*)", str(row.get("email_id", "")))
        if match:
            emails[int(match.group(1))] = row
    for row in draft_rows:
        match = re.fullmatch(r"EM-([1-9][0-9]*)", str(row.get("email_id", "")))
        if match:
            drafts[int(match.group(1))] = row
    return emails, drafts


def _draft_created_at_by_record(
    db: Session,
    *,
    company_id: int,
    record_ids: set[int],
) -> dict[int, datetime]:
    if not record_ids:
        return {}
    rows = db.execute(
        select(
            CompanyActivityEvent.processed_email_id,
            CompanyActivityEvent.occurred_at,
        ).where(
            CompanyActivityEvent.company_id == company_id,
            CompanyActivityEvent.processed_email_id.in_(record_ids),
            CompanyActivityEvent.event_type == "draft_created",
        )
    ).all()
    return {
        int(record_id): _as_utc(occurred_at)
        for record_id, occurred_at in rows
    }


def _all_company_drafts(
    db: Session,
    *,
    company: Company,
) -> list[DashboardDraftDetail]:
    emails, drafts = _read_sheet_rows(company)
    records = db.scalars(
        select(ProcessedEmail).where(
            ProcessedEmail.company_id == company.id,
            ProcessedEmail.status.in_(("done", "failed")),
            ProcessedEmail.gmail_draft_id.is_not(None),
            ProcessedEmail.draft_status.in_(tuple(_DRAFT_STATUS_LABELS)),
        )
    ).all()
    created_at = _draft_created_at_by_record(
        db,
        company_id=company.id,
        record_ids={int(record.id) for record in records},
    )

    result: list[DashboardDraftDetail] = []
    for record in records:
        record_id = int(record.id)
        email_row = emails.get(record_id)
        draft_row = drafts.get(record_id)
        if email_row is None or draft_row is None:
            continue
        if str(draft_row.get("draft_id", "")) != record.gmail_draft_id:
            continue

        recipient_name, recipient_email = _sender_parts(email_row.get("sender", ""))
        result.append(
            DashboardDraftDetail(
                draft_ref=f"BR-{record_id}",
                recipient_name=recipient_name,
                recipient_email=recipient_email,
                subject=_limited_text(email_row.get("subject", ""), 500),
                received_at=(
                    _as_utc(record.received_at)
                    if record.received_at is not None
                    else None
                ),
                prepared_at=created_at.get(record_id),
                summary=str(email_row.get("summary", "") or "").strip(),
                category=_safe_label(
                    email_row.get("category", ""),
                    labels=CATEGORY_LABELS,
                    database_value=record.category,
                ),
                priority=_safe_label(
                    email_row.get("priority", ""),
                    labels=PRIORITY_LABELS,
                    database_value=record.priority,
                ),
                status=(
                    "En erreur"
                    if record.status == "failed"
                    else _DRAFT_STATUS_LABELS[record.draft_status]
                ),
                content=str(draft_row.get("content", "") or "")[:50000],
                ai_generated=True,
            )
        )

    minimum = datetime.min.replace(tzinfo=UTC)
    result.sort(
        key=lambda item: (
            item.prepared_at or item.received_at or minimum,
            int(item.draft_ref[3:]),
        ),
        reverse=True,
    )
    return result


def _item(detail: DashboardDraftDetail) -> DashboardDraftItem:
    return DashboardDraftItem(
        draft_ref=detail.draft_ref,
        recipient_name=detail.recipient_name,
        recipient_email=detail.recipient_email,
        subject=detail.subject,
        received_at=detail.received_at,
        prepared_at=detail.prepared_at,
        summary=detail.summary,
        category=detail.category,
        priority=detail.priority,
        status=detail.status,
    )


def list_company_drafts(
    db: Session,
    *,
    company: Company,
    page: int,
    page_size: int,
    search: str | None = None,
    status: str | None = None,
) -> DashboardDraftsResponse:
    """Recherche et pagine les brouillons rapprochés avec le tenant courant."""
    all_drafts = _all_company_drafts(db, company=company)
    counts = Counter(draft.status for draft in all_drafts)
    metrics = DashboardDraftMetrics(
        pending=counts["En attente"],
        sent=counts["Envoyé"],
        ignored=counts["Ignoré"],
        errors=counts["En erreur"],
    )

    needle = _normalize(search)
    status_key = _normalize(status)
    filtered = all_drafts
    if needle:
        terms = needle.split()
        filtered = [
            draft
            for draft in filtered
            if all(
                term
                in _normalize(
                    " ".join(
                        (
                            draft.recipient_name,
                            draft.recipient_email,
                            draft.subject,
                            draft.content,
                            draft.category,
                            draft.summary,
                        )
                    )
                )
                for term in terms
            )
        ]
    if status_key:
        filtered = [
            draft for draft in filtered if _normalize(draft.status) == status_key
        ]

    total = len(filtered)
    total_pages = (total + page_size - 1) // page_size
    start = (page - 1) * page_size
    return DashboardDraftsResponse(
        items=[_item(draft) for draft in filtered[start : start + page_size]],
        pagination=DashboardDraftPagination(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
        ),
        metrics=metrics,
    )


def get_company_draft(
    db: Session,
    *,
    company: Company,
    draft_ref: str,
) -> DashboardDraftDetail | None:
    """Retourne un brouillon seulement après rapprochement DB, tenant et Sheet."""
    record_id = _record_id(draft_ref)
    if record_id is None:
        return None
    return next(
        (
            draft
            for draft in _all_company_drafts(db, company=company)
            if draft.draft_ref == draft_ref
        ),
        None,
    )


def ignore_company_draft(
    db: Session,
    *,
    company: Company,
    draft_ref: str,
) -> DashboardDraftIgnoreResponse | None:
    """Ignore sans suppression Gmail, après résolution du tenant authentifié."""
    record_id = _record_id(draft_ref)
    if record_id is None:
        return None
    record = db.scalar(
        select(ProcessedEmail).where(
            ProcessedEmail.id == record_id,
            ProcessedEmail.company_id == company.id,
            ProcessedEmail.status == "done",
            ProcessedEmail.gmail_draft_id.is_not(None),
            ProcessedEmail.draft_status.is_not(None),
        )
    )
    if record is None:
        return None

    if record.draft_status == "pending":
        if not claim_draft_discard(
            db,
            company_id=company.id,
            record_id=record_id,
        ):
            current_status = db.scalar(
                select(ProcessedEmail.draft_status).where(
                    ProcessedEmail.id == record_id,
                    ProcessedEmail.company_id == company.id,
                )
            )
            if current_status != "discarded":
                raise DraftActionConflictError("draft_state_conflict")
    elif record.draft_status != "discarded":
        raise DraftActionConflictError("draft_not_pending")
    elif record.sheet_status_synced_at is not None:
        return DashboardDraftIgnoreResponse(
            draft_ref=draft_ref,
            status="Ignoré",
            sheet_sync_pending=False,
        )

    sheet_sync_pending = True
    try:
        workbook = get_company_workbook(company.sheet_id)
        update_company_draft_status(
            workbook,
            email_id=f"EM-{record_id}",
            draft_id=record.gmail_draft_id,
            statut="Ignoré",
        )
        sheet_sync_pending = not mark_draft_sheet_synced(
            db,
            company_id=company.id,
            record_id=record_id,
            draft_status="discarded",
        )
    except Exception as exc:
        logger.warning(
            "Synchronisation du statut brouillon différée (%s)",
            type(exc).__name__,
        )
        sheet_sync_pending = True

    return DashboardDraftIgnoreResponse(
        draft_ref=draft_ref,
        status="Ignoré",
        sheet_sync_pending=sheet_sync_pending,
    )
