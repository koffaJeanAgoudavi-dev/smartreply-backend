"""Événements analytiques minimaux du tableau de bord."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

ACTIVITY_EVENT_TYPES = (
    "draft_created",
    "email_processed",
    "email_skipped",
    "notification_sent",
    "draft_sent",
    "draft_discarded",
)


class CompanyActivityEvent(Base):
    """Transition métier sans contenu d'email ni identifiant externe."""

    __tablename__ = "company_activity_events"
    __table_args__ = (
        UniqueConstraint(
            "processed_email_id",
            "event_type",
            name="uq_company_activity_record_event",
        ),
        CheckConstraint(
            "event_type IN ("
            "'draft_created', 'email_processed', 'email_skipped', "
            "'notification_sent', 'draft_sent', 'draft_discarded'"
            ")",
            name="ck_company_activity_event_type",
        ),
        Index(
            "ix_company_activity_company_occurred",
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
    processed_email_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("processed_emails.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
