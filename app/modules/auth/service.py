"""
SmartReply Agent — Auth : logique métier (Étape 2)
==================================================
Crée et authentifie les utilisateurs de la table PostgreSQL "users" (Neon).
N'intervient pas dans le pipeline Gmail → Sheets → Telegram existant.
"""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.auth.security import hash_password, verify_password

# Empreinte factice : uniformise le temps de réponse quand l'email n'existe pas
_DUMMY_HASH = hash_password("smartreply-password-factice")


def normalize_email(email: str) -> str:
    """Email stocké et recherché en minuscules, sans espaces parasites."""
    return email.strip().lower()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.execute(
        select(User).where(User.email == normalize_email(email))
    ).scalar_one_or_none()


def create_user(db: Session, email: str, password: str) -> User:
    """Crée un utilisateur (mot de passe haché Argon2). Lève ValueError si l'email existe déjà."""
    user = User(email=normalize_email(email), hashed_password=hash_password(password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError("Un compte existe déjà pour cet email")
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    """Retourne l'utilisateur si les identifiants sont valides, sinon None."""
    user = get_user_by_email(db, email)
    if user is None:
        verify_password(password, _DUMMY_HASH)  # anti-timing : même coût qu'une vérification réelle
        return None
    return user if verify_password(password, user.hashed_password) else None
