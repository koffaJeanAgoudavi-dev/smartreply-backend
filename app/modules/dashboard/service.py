"""Calculs Neon et lecture sûre du Sheet du propriétaire authentifié."""

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.companies.models import Company
from app.modules.dashboard.models import CompanyActivityEvent
from app.modules.dashboard.schemas import (
    ActivityItem,
    BreakdownItem,
    DashboardMetrics,
    DashboardOverviewResponse,
    RecentEmailItem,
    TimeSeriesPoint,
)
from app.modules.processed_emails.models import ProcessedEmail
from app.modules.sheets.service import (
    get_company_workbook,
    list_company_email_rows,
)

VOLUME_DAYS = 14
RECENT_ACTIVITY_LIMIT = 8
_SHEET_EMAIL_ID = re.compile(r"^EM-([1-9][0-9]*)$")
_EMAIL_ADDRESS = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)

CATEGORY_LABELS = {
    "devis": "Devis",
    "support": "Support",
    "facturation": "Facturation",
    "partenariat": "Partenariat",
    "spam": "Spam",
    "newsletter": "Newsletter",
    "autre": "Autre",
}
PRIORITY_LABELS = {
    "haute": "Haute",
    "moyenne": "Moyenne",
    "basse": "Basse",
}
ACTIVITY_LABELS = {
    "draft_created": "Brouillon Gmail créé",
    "email_processed": "Email traité",
    "email_skipped": "Email ignoré après analyse",
    "notification_sent": "Notification Telegram envoyée",
    "draft_sent": "Brouillon envoyé",
    "draft_discarded": "Brouillon ignoré",
}
SHEET_STATUS_LABELS = {
    "analysé": "Analysé",
    "brouillon prêt": "Brouillon prêt",
    "envoyé": "Envoyé",
    "ignoré": "Ignoré",
}


class CompanySheetReadError(RuntimeError):
    """Le Sheet de l'entreprise ne peut pas être lu sans risque de fuite."""


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _count_records(db: Session, company_id: int, *conditions) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(ProcessedEmail)
            .where(ProcessedEmail.company_id == company_id, *conditions)
        )
        or 0
    )


def _breakdown(
    db: Session,
    *,
    company_id: int,
    column,
    labels: dict[str, str],
) -> list[BreakdownItem]:
    rows = db.execute(
        select(column, func.count())
        .where(
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.status.in_(("done", "skipped")),
            column.is_not(None),
        )
        .group_by(column)
    ).all()
    counts = {str(key): int(count) for key, count in rows if key in labels}
    return [
        BreakdownItem(key=key, label=label, count=counts[key])
        for key, label in labels.items()
        if counts.get(key, 0) > 0
    ]


def get_dashboard_overview(
    db: Session,
    *,
    company_id: int,
    now: datetime | None = None,
) -> DashboardOverviewResponse:
    """Retourne uniquement les agrégats de l'entreprise demandée."""
    metrics = DashboardMetrics(
        analyzed_emails=_count_records(
            db,
            company_id,
            ProcessedEmail.status.in_(("done", "skipped")),
        ),
        processed_emails=_count_records(
            db,
            company_id,
            ProcessedEmail.status == "done",
        ),
        ignored_emails=_count_records(
            db,
            company_id,
            ProcessedEmail.status == "skipped",
        ),
        pending_drafts=_count_records(
            db,
            company_id,
            ProcessedEmail.draft_status == "pending",
        ),
    )

    current_day = _as_utc(now or datetime.now(UTC)).date()
    first_day = current_day - timedelta(days=VOLUME_DAYS - 1)
    event_rows = db.execute(
        select(CompanyActivityEvent.occurred_at)
        .join(
            ProcessedEmail,
            ProcessedEmail.id == CompanyActivityEvent.processed_email_id,
        )
        .where(
            CompanyActivityEvent.company_id == company_id,
            ProcessedEmail.company_id == company_id,
            CompanyActivityEvent.event_type.in_(("email_processed", "email_skipped")),
            CompanyActivityEvent.occurred_at
            >= datetime.combine(first_day, datetime.min.time(), tzinfo=UTC),
        )
    ).scalars()
    daily_counts = Counter(_as_utc(value).date() for value in event_rows)
    volume = [
        TimeSeriesPoint(
            day=first_day + timedelta(days=offset),
            count=daily_counts[first_day + timedelta(days=offset)],
        )
        for offset in range(VOLUME_DAYS)
    ]

    activity_rows = db.execute(
        select(CompanyActivityEvent)
        .join(
            ProcessedEmail,
            ProcessedEmail.id == CompanyActivityEvent.processed_email_id,
        )
        .where(
            CompanyActivityEvent.company_id == company_id,
            ProcessedEmail.company_id == company_id,
        )
        .order_by(
            CompanyActivityEvent.occurred_at.desc(),
            CompanyActivityEvent.id.desc(),
        )
        .limit(RECENT_ACTIVITY_LIMIT)
    ).scalars().all()
    recent_activity = [
        ActivityItem(
            event_type=event.event_type,
            label=ACTIVITY_LABELS[event.event_type],
            occurred_at=_as_utc(event.occurred_at),
        )
        for event in activity_rows
        if event.event_type in ACTIVITY_LABELS
    ]

    return DashboardOverviewResponse(
        metrics=metrics,
        volume=volume,
        categories=_breakdown(
            db,
            company_id=company_id,
            column=ProcessedEmail.category,
            labels=CATEGORY_LABELS,
        ),
        priorities=_breakdown(
            db,
            company_id=company_id,
            column=ProcessedEmail.priority,
            labels=PRIORITY_LABELS,
        ),
        recent_activity=recent_activity,
        history_started=bool(activity_rows),
    )


def _display_sender(raw_sender: str) -> str:
    """Retire toute adresse et ne conserve qu'un nom d'affichage sûr."""
    value = str(raw_sender or "").strip()
    name, address = parseaddr(value)
    display_name = name.strip()
    if display_name and not _EMAIL_ADDRESS.search(display_name):
        return display_name[:160]
    if "@" in address or "@" in value:
        return "Expéditeur"
    return value[:160] or "Expéditeur"


def _limited_text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _safe_label(
    sheet_value: object,
    *,
    labels: dict[str, str],
    database_value: str | None,
) -> str:
    sheet_key = str(sheet_value or "").strip().casefold()
    if sheet_key in labels:
        return labels[sheet_key]
    return labels.get(str(database_value or "").casefold(), "")


def _safe_status_label(
    sheet_value: object,
    *,
    record_status: str,
    draft_status: str | None,
) -> str:
    sheet_key = str(sheet_value or "").strip().casefold()
    if sheet_key in SHEET_STATUS_LABELS:
        return SHEET_STATUS_LABELS[sheet_key]
    if draft_status == "pending":
        return "Brouillon prêt"
    if draft_status == "sent":
        return "Envoyé"
    if draft_status == "discarded" or record_status == "skipped":
        return "Ignoré"
    return "Analysé" if record_status == "done" else ""


def list_recent_company_emails(
    db: Session,
    *,
    company: Company,
    limit: int,
) -> list[RecentEmailItem]:
    """Lit le Sheet explicite puis valide chaque ligne contre Neon et le tenant."""
    try:
        workbook = get_company_workbook(company.sheet_id)
        sheet_rows = list_company_email_rows(workbook)
    except Exception as exc:
        raise CompanySheetReadError("company_sheet_unavailable") from exc

    candidate_rows: list[tuple[int, dict[str, str]]] = []
    seen_ids: set[int] = set()
    for row in reversed(sheet_rows):
        match = _SHEET_EMAIL_ID.fullmatch(str(row.get("email_id", "")))
        if match is None:
            continue
        record_id = int(match.group(1))
        if record_id in seen_ids:
            continue
        seen_ids.add(record_id)
        candidate_rows.append((record_id, row))

    if not candidate_rows:
        return []

    candidate_ids = {record_id for record_id, _ in candidate_rows}
    records = db.execute(
        select(
            ProcessedEmail.id,
            ProcessedEmail.received_at,
            ProcessedEmail.category,
            ProcessedEmail.priority,
            ProcessedEmail.status,
            ProcessedEmail.draft_status,
        ).where(
            ProcessedEmail.company_id == company.id,
            ProcessedEmail.id.in_(candidate_ids),
        )
    ).all()
    records_by_id = {int(record.id): record for record in records}

    result: list[RecentEmailItem] = []
    for record_id, row in candidate_rows:
        record = records_by_id.get(record_id)
        if record is None:
            continue
        result.append(
            RecentEmailItem(
                sender=_display_sender(row.get("sender", "")),
                subject=_limited_text(row.get("subject", ""), 500),
                received_at=(
                    _as_utc(record.received_at)
                    if record.received_at is not None
                    else None
                ),
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
        )
        if len(result) >= limit:
            break
    return result
