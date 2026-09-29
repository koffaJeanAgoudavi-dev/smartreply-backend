"""
SmartReply Agent — Étape 3 : modèle Connexion Gmail (table PostgreSQL "gmail_connections")
=========================================================================================
Lien 1-1 entre un utilisateur (users.id) et SON Gmail. Le refresh token OAuth
y est stocké CHIFFRÉ (Fernet, clé TOKEN_ENCRYPTION_KEY) — jamais en clair,
jamais dans un Google Sheet, jamais renvoyé par une API.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class GmailConnection(Base):
    """Connexion Gmail d'un utilisateur (refresh token chiffré)."""

    __tablename__ = "gmail_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), unique=True, nullable=False
    )
    encrypted_refresh_token: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
