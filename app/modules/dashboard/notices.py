"""Bannière d'information administrateur limitée à l'entreprise du JWT."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.admin.models import AdminUserMessage


def _serialize_notice(message: AdminUserMessage) -> dict[str, object]:
    return {
        "notice_ref": f"INFO-{message.id}",
        "title": message.title,
        "body": message.body,
        "created_at": message.created_at,
    }


def get_latest_unread_company_notice(
    db: Session,
    *,
    company_id: int,
) -> dict[str, object]:
    """Retourne uniquement le message non lu à afficher dans la bannière."""

    latest_unread = db.scalar(
        select(AdminUserMessage)
        .where(
            AdminUserMessage.company_id == company_id,
            AdminUserMessage.read_at.is_(None),
        )
        .order_by(
            AdminUserMessage.created_at.desc(),
            AdminUserMessage.id.desc(),
        )
        .limit(1)
    )
    return {
        "latest_unread": (
            _serialize_notice(latest_unread) if latest_unread is not None else None
        )
    }


def mark_company_notice_read(
    db: Session,
    *,
    company_id: int,
    message_id: int,
    now: datetime | None = None,
) -> dict[str, object] | None:
    message = db.scalar(
        select(AdminUserMessage).where(
            AdminUserMessage.id == message_id,
            AdminUserMessage.company_id == company_id,
        )
    )
    if message is None:
        return None
    if message.read_at is None:
        message.read_at = now or datetime.now(UTC)
        db.commit()
        db.refresh(message)
    return {
        "notice_ref": f"INFO-{message.id}",
        "read_at": message.read_at,
    }
