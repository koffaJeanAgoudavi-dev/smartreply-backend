"""
SmartReply Agent — Étape 2 : modèle Utilisateur (table PostgreSQL "users")
=========================================================================
Stockée dans la base Neon (DATABASE_URL) : id, email, mot de passe haché
(Argon2, jamais en clair), date de création. Les refresh tokens Gmail par
utilisateur viendront plus tard dans cette même base, jamais dans un Sheet.
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class User(Base):
    """Utilisateur de la couche multi-utilisateur (Étape 2)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
