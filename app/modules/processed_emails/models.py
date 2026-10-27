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
        Index(
            "ix_processed_emails_company_status",
            "company_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    gmail_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
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
