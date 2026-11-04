"""Modèle SQLAlchemy de l'entreprise et de son Google Sheet dédié."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Company(Base):
    """Entreprise unique liée à son Sheet historique ou détenu par le client."""

    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(
            "sheet_owner_mode IN ('platform', 'client')",
            name="ck_companies_sheet_owner_mode",
        ),
        CheckConstraint(
            "response_tone IN ('professional', 'friendly', 'direct', 'warm')",
            name="ck_companies_response_tone",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), unique=True, nullable=False
    )
    nom_entreprise: Mapped[str] = mapped_column(String(255), nullable=False)
    secteur: Mapped[str] = mapped_column(String(255), nullable=False)
    response_tone: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="professional",
        server_default="professional",
    )
    sheet_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # ``platform`` conserve la compatibilité des Sheets historiques. Toutes les
    # nouvelles créations utilisent ``client`` et exigent une connexion Drive.
    sheet_owner_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="platform",
        server_default="platform",
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
    # Ces deux dates restent nulles pendant la simulation de la PR 1.
    # La date d'activation sera fixée au premier traitement réel (PR 2).
    pipeline_activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pipeline_last_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
