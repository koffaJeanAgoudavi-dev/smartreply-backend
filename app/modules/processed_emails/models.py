"""Modèle du registre de traitement des emails, isolé par entreprise."""

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

PROCESSED_EMAIL_STATUSES = ("done", "skipped", "failed", "processing")


class ProcessedEmail(Base):
    """État idempotent d'un message Gmail pour une entreprise."""

    __tablename__ = "processed_emails"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "gmail_message_id",
            name="uq_processed_emails_company_message",
        ),
        CheckConstraint(
            "status IN ('done', 'skipped', 'failed', 'processing')",
            name="ck_processed_emails_status",
        ),
        CheckConstraint(
            "attempts >= 0 AND attempts <= 3",
            name="ck_processed_emails_attempts",
        ),
        CheckConstraint(
            "draft_status IS NULL OR draft_status IN "
            "('pending', 'sending', 'sent', 'discarded', 'missing')",
            name="ck_processed_emails_draft_status",
        ),
        CheckConstraint(
            "notification_attempts >= 0 AND notification_attempts <= 3",
            name="ck_processed_emails_notification_attempts",
        ),
        CheckConstraint(
            "category IS NULL OR category IN "
            "('devis', 'support', 'facturation', 'partenariat', "
            "'spam', 'newsletter', 'autre')",
            name="ck_processed_emails_category",
        ),
        CheckConstraint(
            "priority IS NULL OR priority IN ('haute', 'moyenne', 'basse')",
            name="ck_processed_emails_priority",
        ),
        Index(
            "ix_processed_emails_company_status",
            "company_id",
            "status",
        ),
        Index(
            "uq_processed_emails_callback_token",
            "callback_token",
            unique=True,
        ),
        Index(
            "ix_processed_emails_company_notification",
            "company_id",
            "notified_at",
            "notification_attempts",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    gmail_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    gmail_draft_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    callback_token: Mapped[str | None] = mapped_column(String(43), nullable=True)
    draft_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="processing",
        server_default="processing",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notification_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    notification_blocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notification_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sheet_status_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
