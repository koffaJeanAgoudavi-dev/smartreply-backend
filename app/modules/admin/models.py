"""Messages internes et audit minimal des actions administrateur V2."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

ADMIN_AUDIT_EVENT_TYPES = (
    "message_sent",
    "company_suspended",
    "company_resumed",
)


class AdminUserMessage(Base):
    """Information interne visible uniquement par le propriétaire de l'entreprise."""

    __tablename__ = "admin_user_messages"
    __table_args__ = (
        Index(
            "ix_admin_user_messages_company_created",
            "company_id",
            "created_at",
        ),
        Index(
            "ix_admin_user_messages_company_read",
            "company_id",
            "read_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AdminAuditEvent(Base):
    """Trace sûre d'une action admin, sans contenu du message ni secret."""

    __tablename__ = "admin_audit_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ("
            "'message_sent', 'company_suspended', 'company_resumed'"
            ")",
            name="ck_admin_audit_event_type",
        ),
        Index(
            "ix_admin_audit_company_occurred",
            "company_id",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    related_message_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("admin_user_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
