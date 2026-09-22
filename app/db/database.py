"""
SmartReply Agent — Étape 2 : connexion PostgreSQL (Neon)
========================================================
Engine SQLAlchemy 2.0 pointé sur la variable d'environnement DATABASE_URL
(base Neon déjà créée, connexion pooled (-pooler dans l'hôte), configurée
sur Render). Aucune autre base de données n'est utilisée.

 réservée à la couche multi-utilisateur (module auth) ; les modules 1→7
(Gmail, filtrage, IA, Sheets, brouillons, notifications, actions, pipeline)
continuent de fonctionner sans base de données, comme avant.
"""
from typing import Generator

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """Base déclarative commune : metadata des tables PostgreSQL."""


def _normalize_url(url: str) -> str:
    """SQLAlchemy 2.0 refuse le schéma hérité postgres:// → postgresql://."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


DATABASE_URL = _normalize_url(settings.DATABASE_URL)

# pool_pre_ping : évite les connexions périmées du pooler Neon
engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Dépendance FastAPI : une session par requête, fermée ensuite."""
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Base de données non configurée (variable DATABASE_URL manquante)",
        )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
