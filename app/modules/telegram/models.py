"""Modèles PostgreSQL de liaison d'un compte SmartReply à Telegram."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

LINK_CODE_STATUSES = ("pending", "used", "expired")


class TelegramLinkCode(Base):
    """Code temporaire, à usage unique, généré pour un utilisateur."""

    __tablename__ = "telegram_link_codes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'used', 'expired')",
            name="ck_telegram_link_codes_status",
        ),
        Index(
            "uq_telegram_link_codes_pending_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TelegramConnection(Base):
    """Liaison 1-1 entre un utilisateur SmartReply et un chat Telegram."""

    __tablename__ = "telegram_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        unique=True,
        nullable=False,
    )
    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
