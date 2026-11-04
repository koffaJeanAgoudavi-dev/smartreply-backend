"""Connexion OAuth Drive propre à chaque utilisateur.

Le refresh token est chiffré avec la même clé Fernet que les connexions Gmail,
mais il reste dans une table séparée afin de conserver des consentements et des
cycles de révocation indépendants.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class DriveConnection(Base):
    """Autorisation ``drive.file`` d'un utilisateur, stockée chiffrée."""

    __tablename__ = "drive_connections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('connected', 'revoked')",
            name="ck_drive_connections_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        unique=True,
        nullable=False,
    )
    encrypted_refresh_token: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="connected",
        server_default="connected",
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
