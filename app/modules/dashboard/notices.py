"""Messages d'information administrateur limités à l'entreprise du JWT."""

import math
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.admin.models import AdminUserMessage


def _serialize_notice(message: AdminUserMessage) -> dict[str, object]:
    return {
        "notice_ref": f"INFO-{message.id}",
        "title": message.title,
        "body": message.body,
        "created_at": message.created_at,
        "read_at": message.read_at,
    }


def list_company_notices(
    db: Session,
    *,
    company_id: int,
    page: int,
    page_size: int,
) -> dict[str, object]:
    conditions = (AdminUserMessage.company_id == company_id,)
    total_items = int(
        db.scalar(
            select(func.count(AdminUserMessage.id)).where(*conditions)
        )
        or 0
    )
    unread_count = int(
        db.scalar(
            select(func.count(AdminUserMessage.id)).where(
                *conditions,
                AdminUserMessage.read_at.is_(None),
            )
        )
        or 0
    )
    order = (
        AdminUserMessage.created_at.desc(),
        AdminUserMessage.id.desc(),
    )
    messages = db.execute(
        select(AdminUserMessage)
        .where(*conditions)
        .order_by(*order)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    latest_unread = db.scalar(
        select(AdminUserMessage)
        .where(
            *conditions,
            AdminUserMessage.read_at.is_(None),
        )
        .order_by(*order)
        .limit(1)
    )
    return {
        "items": [_serialize_notice(message) for message in messages],
        "latest_unread": (
            _serialize_notice(latest_unread) if latest_unread is not None else None
        ),
        "unread_count": unread_count,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": math.ceil(total_items / page_size) if total_items else 0,
        },
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
