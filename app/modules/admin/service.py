"""Lectures et actions V2 bornées de la console administrateur."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.company_allowlist import parse_allowed_company_ids
from app.core.config import settings
from app.modules.admin.models import AdminAuditEvent, AdminUserMessage
from app.modules.auth.models import User
from app.modules.companies.models import Company
from app.modules.companies.operational_status import as_utc, is_company_suspended
from app.modules.drive_oauth.models import DriveConnection
from app.modules.gmail_oauth.models import GmailConnection
from app.modules.processed_emails.models import ProcessedEmail
from app.modules.telegram.models import TelegramConnection

_PROFILE_FIELDS = (
    "business_description",
    "products_services",
    "business_hours",
    "response_signature",
    "business_rules",
    "response_instructions",
)
_ADMIN_AUDIT_LABELS = {
    "message_sent": "Message d’information envoyé",
    "company_suspended": "Entreprise suspendue",
    "company_resumed": "Entreprise réactivée",
}
_ADMIN_AUDIT_LIMIT = 20


class AdminCompanyNotFoundError(LookupError):
    """L'entreprise ciblée n'existe pas."""


class AdminActionConflictError(RuntimeError):
    """L'état actuel ne permet pas l'action demandée."""


class AdminActionPersistenceError(RuntimeError):
    """L'action n'a pas pu être enregistrée atomiquement."""


def _email_metrics_subquery():
    return (
        select(
            ProcessedEmail.company_id.label("company_id"),
            func.sum(
                case(
                    (ProcessedEmail.status.in_(("done", "skipped")), 1),
                    else_=0,
                )
            ).label("processed_emails"),
            func.sum(
                case((ProcessedEmail.status == "failed", 1), else_=0)
            ).label("failed_emails"),
            func.sum(
                case((ProcessedEmail.draft_status == "pending", 1), else_=0)
            ).label("pending_drafts"),
        )
        .group_by(ProcessedEmail.company_id)
        .subquery()
    )


def _company_query():
    metrics = _email_metrics_subquery()
    return (
        select(
            Company,
            User.email,
            GmailConnection.status,
            GmailConnection.created_at,
            GmailConnection.updated_at,
            DriveConnection.status,
            DriveConnection.created_at,
            DriveConnection.updated_at,
            TelegramConnection.connected_at,
            func.coalesce(metrics.c.processed_emails, 0),
            func.coalesce(metrics.c.failed_emails, 0),
            func.coalesce(metrics.c.pending_drafts, 0),
        )
        .join(User, User.id == Company.user_id)
        .outerjoin(GmailConnection, GmailConnection.user_id == Company.user_id)
        .outerjoin(DriveConnection, DriveConnection.user_id == Company.user_id)
        .outerjoin(TelegramConnection, TelegramConnection.user_id == Company.user_id)
        .outerjoin(metrics, metrics.c.company_id == Company.id)
    )


def _connection_status(value: str | None) -> Literal["connected", "revoked", "missing"]:
    if value == "connected":
        return "connected"
    if value == "revoked":
        return "revoked"
    return "missing"


def _row_to_company_summary(row: Any, allowed_ids: frozenset[int]) -> dict[str, Any]:
    company: Company = row[0]
    gmail_status = _connection_status(row[2])
    drive_status = _connection_status(row[5])
    sheet_status = "configured" if company.sheet_id.strip() else "missing"
    suspended = is_company_suspended(company)
    return {
        "company_id": company.id,
        "name": company.nom_entreprise,
        "sector": company.secteur,
        "owner_email": row[1],
        "created_at": company.created_at,
        "validation_status": (
            "validated" if company.id in allowed_ids else "pending"
        ),
        "configuration_status": (
            "ready"
            if gmail_status == "connected" and sheet_status == "configured"
            else "incomplete"
        ),
        "operational_status": "suspended" if suspended else "active",
        "suspended_until": company.suspended_until if suspended else None,
        "gmail_status": gmail_status,
        "drive_status": drive_status,
        "sheet_status": sheet_status,
        "telegram_connected": row[8] is not None,
        "pipeline_activated_at": company.pipeline_activated_at,
        "pipeline_last_processed_at": company.pipeline_last_processed_at,
        "processed_emails": int(row[9] or 0),
        "failed_emails": int(row[10] or 0),
        "pending_drafts": int(row[11] or 0),
    }


def _escaped_like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped.lower()}%"


def get_overview(db: Session) -> dict[str, Any]:
    """Agrège l'état existant sans appeler Gmail, Drive, Sheets ou Telegram."""

    allowed_ids = parse_allowed_company_ids()
    users = int(db.scalar(select(func.count(User.id))) or 0)
    companies = int(db.scalar(select(func.count(Company.id))) or 0)
    validated = (
        int(
            db.scalar(
                select(func.count(Company.id)).where(Company.id.in_(allowed_ids))
            )
            or 0
        )
        if allowed_ids
        else 0
    )

    ready_condition = and_(
        GmailConnection.status == "connected",
        func.length(func.trim(Company.sheet_id)) > 0,
    )
    ready = int(
        db.scalar(
            select(func.count(Company.id))
            .select_from(Company)
            .outerjoin(
                GmailConnection,
                GmailConnection.user_id == Company.user_id,
            )
            .where(ready_condition)
        )
        or 0
    )
    gmail_issues = int(
        db.scalar(
            select(func.count(Company.id))
            .select_from(Company)
            .outerjoin(
                GmailConnection,
                GmailConnection.user_id == Company.user_id,
            )
            .where(
                or_(
                    GmailConnection.id.is_(None),
                    GmailConnection.status != "connected",
                )
            )
        )
        or 0
    )
    drive_issues = int(
        db.scalar(
            select(func.count(Company.id))
            .select_from(Company)
            .outerjoin(
                DriveConnection,
                DriveConnection.user_id == Company.user_id,
            )
            .where(
                Company.sheet_owner_mode == "client",
                or_(
                    DriveConnection.id.is_(None),
                    DriveConnection.status != "connected",
                ),
            )
        )
        or 0
    )
    telegram_connected = int(
        db.scalar(select(func.count(TelegramConnection.id))) or 0
    )

    processed_metrics = db.execute(
        select(
            func.sum(
                case(
                    (ProcessedEmail.status.in_(("done", "skipped")), 1),
                    else_=0,
                )
            ),
            func.sum(case((ProcessedEmail.status == "failed", 1), else_=0)),
            func.sum(
                case((ProcessedEmail.draft_status == "pending", 1), else_=0)
            ),
        )
    ).one()
    last_processing_at = db.scalar(select(func.max(Company.pipeline_last_processed_at)))
    incidents = _collect_incidents(db)

    return {
        "metrics": {
            "users": users,
            "companies": companies,
            "validated_companies": validated,
            "pending_companies": max(0, companies - validated),
            "ready_companies": ready,
            "incomplete_companies": max(0, companies - ready),
            "gmail_issues": gmail_issues,
            "drive_issues": drive_issues,
            "telegram_connected": telegram_connected,
            "processed_emails": int(processed_metrics[0] or 0),
            "failed_emails": int(processed_metrics[1] or 0),
            "pending_drafts": int(processed_metrics[2] or 0),
            "active_incidents": len(incidents),
        },
        "last_processing_at": last_processing_at,
        "generated_at": datetime.now(timezone.utc),
    }


def list_companies(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: str | None,
    validation_status: str | None,
    configuration_status: str | None,
    telegram_connected: bool | None,
) -> dict[str, Any]:
    """Liste paginée des entreprises, sans identifiant externe ni profil texte."""

    allowed_ids = parse_allowed_company_ids()
    statement = _company_query()

    normalized_search = (search or "").strip()
    if normalized_search:
        pattern = _escaped_like(normalized_search)
        statement = statement.where(
            or_(
                func.lower(Company.nom_entreprise).like(pattern, escape="\\"),
                func.lower(Company.secteur).like(pattern, escape="\\"),
                func.lower(User.email).like(pattern, escape="\\"),
            )
        )

    if validation_status == "validated":
        statement = statement.where(
            Company.id.in_(allowed_ids) if allowed_ids else false()
        )
    elif validation_status == "pending" and allowed_ids:
        statement = statement.where(Company.id.not_in(allowed_ids))

    ready_condition = and_(
        GmailConnection.status == "connected",
        func.length(func.trim(Company.sheet_id)) > 0,
    )
    if configuration_status == "ready":
        statement = statement.where(ready_condition)
    elif configuration_status == "incomplete":
        statement = statement.where(~ready_condition)

    if telegram_connected is True:
        statement = statement.where(TelegramConnection.id.is_not(None))
    elif telegram_connected is False:
        statement = statement.where(TelegramConnection.id.is_(None))

    total_items = int(
        db.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        or 0
    )
    rows = db.execute(
        statement.order_by(Company.created_at.desc(), Company.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return {
        "items": [_row_to_company_summary(row, allowed_ids) for row in rows],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": math.ceil(total_items / page_size) if total_items else 0,
        },
    }


def get_company_detail(db: Session, company_id: int) -> dict[str, Any] | None:
    """Retourne uniquement la complétude du profil et les métadonnées sûres."""

    allowed_ids = parse_allowed_company_ids()
    row = db.execute(_company_query().where(Company.id == company_id)).one_or_none()
    if row is None:
        return None

    company: Company = row[0]
    suspended = is_company_suspended(company)
    audit_rows = db.execute(
        select(
            AdminAuditEvent.event_type,
            User.email,
            AdminAuditEvent.occurred_at,
        )
        .join(User, User.id == AdminAuditEvent.actor_user_id)
        .where(AdminAuditEvent.company_id == company_id)
        .order_by(
            AdminAuditEvent.occurred_at.desc(),
            AdminAuditEvent.id.desc(),
        )
        .limit(_ADMIN_AUDIT_LIMIT)
    ).all()
    detail = _row_to_company_summary(row, allowed_ids)
    detail.update(
        {
            "updated_at": company.updated_at,
            "sheet_owner_mode": company.sheet_owner_mode,
            "suspended_at": company.suspended_at if suspended else None,
            "suspension_reason": company.suspension_reason if suspended else None,
            "gmail_connected_at": row[3],
            "gmail_updated_at": row[4],
            "drive_connected_at": row[6],
            "drive_updated_at": row[7],
            "telegram_connected_at": row[8],
            "profile": {
                "configured_fields": sum(
                    1
                    for field_name in _PROFILE_FIELDS
                    if str(getattr(company, field_name, "")).strip()
                ),
                "total_fields": len(_PROFILE_FIELDS),
                "response_tone": company.response_tone,
            },
            "audit_events": [
                {
                    "event_type": audit_row.event_type,
                    "label": _ADMIN_AUDIT_LABELS[audit_row.event_type],
                    "actor_email": audit_row.email,
                    "occurred_at": audit_row.occurred_at,
                }
                for audit_row in audit_rows
                if audit_row.event_type in _ADMIN_AUDIT_LABELS
            ],
        }
    )
    return detail


def _required_company(db: Session, company_id: int) -> Company:
    company = db.get(Company, company_id)
    if company is None:
        raise AdminCompanyNotFoundError("Entreprise introuvable")
    return company


def _commit_admin_action(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise AdminActionPersistenceError(
            "Action administrateur non enregistrée"
        ) from exc


def send_company_information_message(
    db: Session,
    *,
    company_id: int,
    actor_user_id: int,
    title: str,
    body: str,
) -> AdminUserMessage:
    """Crée un message interne et son audit dans la même transaction."""

    _required_company(db, company_id)
    message = AdminUserMessage(
        company_id=company_id,
        created_by_user_id=actor_user_id,
        title=title.strip(),
        body=body.strip(),
    )
    db.add(message)
    try:
        db.flush()
    except SQLAlchemyError as exc:
        db.rollback()
        raise AdminActionPersistenceError(
            "Message administrateur non enregistré"
        ) from exc
    db.add(
        AdminAuditEvent(
            company_id=company_id,
            actor_user_id=actor_user_id,
            event_type="message_sent",
            related_message_id=message.id,
        )
    )
    _commit_admin_action(db)
    db.refresh(message)
    return message


def suspend_company(
    db: Session,
    *,
    company_id: int,
    actor_user_id: int,
    reason: str,
    suspended_until: datetime | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Suspend le pipeline sans modifier la validation ni les intégrations."""

    company = _required_company(db, company_id)
    current_time = as_utc(now or datetime.now(timezone.utc))
    normalized_until = (
        as_utc(suspended_until) if suspended_until is not None else None
    )
    if normalized_until is not None and normalized_until <= current_time:
        raise AdminActionConflictError(
            "La date de réactivation doit être dans le futur"
        )
    company.suspended_at = current_time
    company.suspended_until = normalized_until
    company.suspension_reason = reason.strip()
    db.add(
        AdminAuditEvent(
            company_id=company_id,
            actor_user_id=actor_user_id,
            event_type="company_suspended",
        )
    )
    _commit_admin_action(db)
    return {
        "company_id": company.id,
        "operational_status": "suspended",
        "suspended_at": company.suspended_at,
        "suspended_until": company.suspended_until,
        "suspension_reason": company.suspension_reason,
    }


def resume_company(
    db: Session,
    *,
    company_id: int,
    actor_user_id: int,
) -> dict[str, Any]:
    """Réactive explicitement une entreprise et conserve la trace d'audit."""

    company = _required_company(db, company_id)
    if company.suspended_at is None:
        raise AdminActionConflictError("L'entreprise est déjà active")
    company.suspended_at = None
    company.suspended_until = None
    company.suspension_reason = None
    db.add(
        AdminAuditEvent(
            company_id=company_id,
            actor_user_id=actor_user_id,
            event_type="company_resumed",
        )
    )
    _commit_admin_action(db)
    return {
        "company_id": company.id,
        "operational_status": "active",
        "suspended_at": None,
        "suspended_until": None,
        "suspension_reason": None,
    }


def _aware_sort_value(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _collect_incidents(
    db: Session,
    *,
    company_id: int | None = None,
) -> list[dict[str, Any]]:
    incidents: list[dict[str, Any]] = []

    gmail_statement = (
        select(
            Company.id,
            Company.nom_entreprise,
            GmailConnection.updated_at,
        )
        .join(User, User.id == Company.user_id)
        .join(GmailConnection, GmailConnection.user_id == User.id)
        .where(GmailConnection.status == "revoked")
    )
    drive_statement = (
        select(
            Company.id,
            Company.nom_entreprise,
            DriveConnection.updated_at,
        )
        .join(User, User.id == Company.user_id)
        .join(DriveConnection, DriveConnection.user_id == User.id)
        .where(
            Company.sheet_owner_mode == "client",
            DriveConnection.status == "revoked",
        )
    )
    if company_id is not None:
        gmail_statement = gmail_statement.where(Company.id == company_id)
        drive_statement = drive_statement.where(Company.id == company_id)

    for row in db.execute(gmail_statement):
        incidents.append(
            {
                "incident_ref": f"INC-GMAIL-{row[0]}",
                "incident_type": "gmail_revoked",
                "label": "Connexion Gmail révoquée",
                "severity": "critical",
                "company_id": row[0],
                "company_name": row[1],
                "code": None,
                "attempts": None,
                "occurred_at": row[2],
            }
        )
    for row in db.execute(drive_statement):
        incidents.append(
            {
                "incident_ref": f"INC-DRIVE-{row[0]}",
                "incident_type": "drive_revoked",
                "label": "Autorisation Google Drive révoquée",
                "severity": "warning",
                "company_id": row[0],
                "company_name": row[1],
                "code": None,
                "attempts": None,
                "occurred_at": row[2],
            }
        )

    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.PIPELINE_PROCESSING_STALE_SECONDS
    )
    record_statement = (
        select(ProcessedEmail, Company.nom_entreprise)
        .join(Company, Company.id == ProcessedEmail.company_id)
        .where(
            or_(
                ProcessedEmail.status == "failed",
                and_(
                    ProcessedEmail.status == "processing",
                    ProcessedEmail.updated_at < cutoff,
                ),
                ProcessedEmail.notification_blocked_at.is_not(None),
                and_(
                    ProcessedEmail.draft_status.in_(("sent", "discarded")),
                    ProcessedEmail.sheet_status_synced_at.is_(None),
                ),
            )
        )
    )
    if company_id is not None:
        record_statement = record_statement.where(
            ProcessedEmail.company_id == company_id
        )

    for record, company_name in db.execute(record_statement):
        if record.status == "failed":
            incidents.append(
                {
                    "incident_ref": f"INC-FAILED-{record.id}",
                    "incident_type": "processing_failed",
                    "label": "Traitement email en échec",
                    "severity": "critical",
                    "company_id": record.company_id,
                    "company_name": company_name,
                    "code": record.last_error_code,
                    "attempts": record.attempts,
                    "occurred_at": record.updated_at,
                }
            )
        if record.status == "processing" and _aware_sort_value(
            record.updated_at
        ) < _aware_sort_value(cutoff):
            incidents.append(
                {
                    "incident_ref": f"INC-STALE-{record.id}",
                    "incident_type": "processing_stale",
                    "label": "Traitement bloqué",
                    "severity": "critical",
                    "company_id": record.company_id,
                    "company_name": company_name,
                    "code": record.last_error_code,
                    "attempts": record.attempts,
                    "occurred_at": record.updated_at,
                }
            )
        if record.notification_blocked_at is not None:
            incidents.append(
                {
                    "incident_ref": f"INC-NOTIFICATION-{record.id}",
                    "incident_type": "notification_blocked",
                    "label": "Notification Telegram bloquée",
                    "severity": "warning",
                    "company_id": record.company_id,
                    "company_name": company_name,
                    "code": None,
                    "attempts": record.notification_attempts,
                    "occurred_at": record.notification_blocked_at,
                }
            )
        if (
            record.draft_status in {"sent", "discarded"}
            and record.sheet_status_synced_at is None
        ):
            incidents.append(
                {
                    "incident_ref": f"INC-SHEET-{record.id}",
                    "incident_type": "sheet_sync_pending",
                    "label": "Synchronisation du statut Sheet en attente",
                    "severity": "warning",
                    "company_id": record.company_id,
                    "company_name": company_name,
                    "code": None,
                    "attempts": record.attempts,
                    "occurred_at": record.updated_at,
                }
            )

    incidents.sort(
        key=lambda incident: _aware_sort_value(incident["occurred_at"]),
        reverse=True,
    )
    return incidents


def list_incidents(
    db: Session,
    *,
    page: int,
    page_size: int,
    company_id: int | None,
    severity: str | None,
) -> dict[str, Any]:
    incidents = _collect_incidents(db, company_id=company_id)
    if severity in {"critical", "warning"}:
        incidents = [item for item in incidents if item["severity"] == severity]

    total_items = len(incidents)
    start = (page - 1) * page_size
    return {
        "items": incidents[start : start + page_size],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": math.ceil(total_items / page_size) if total_items else 0,
        },
    }
