"""
SmartReply Agent — Gmail OAuth : logique métier (Étape 3)
========================================================
Liaison utilisateur ↔ Gmail : URL de consentement (scope gmail.modify UNIQUEMENT),
échange code → refresh token, stockage chiffré dans la table gmail_connections.
Le refresh token n'apparaît jamais dans une réponse API ni dans un log.
"""

import urllib.parse

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.auth.models import User
from app.modules.companies.models import Company
from app.modules.gmail_oauth.models import GmailConnection
from app.modules.gmail_oauth.security import encrypt_token

# Scope unique demandé — aucun droit au-delà de la modification Gmail
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def redirect_uri() -> str:
    """Construit l'URI de redirection OAuth depuis BASE_URL."""
    if not settings.BASE_URL:
        raise RuntimeError(
            "BASE_URL manquant : définissez l'URL publique du service (Render)."
        )
    base_url = settings.BASE_URL.rstrip("/")
    parsed_base_url = urllib.parse.urlsplit(base_url)
    if settings.ENVIRONMENT.strip().lower() == "production" and (
        parsed_base_url.scheme != "https" or not parsed_base_url.netloc
    ):
        raise RuntimeError("BASE_URL doit être une URL HTTPS en production.")
    return base_url + "/api/v1/gmail/callback"


def build_authorization_url(state: str) -> str:
    """URL de consentement Google — access_type=offline + prompt=consent pour obtenir
    un refresh token à chaque connexion."""
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)


async def exchange_code_for_refresh_token(code: str) -> str:
    """Échange le code d'autorisation contre un refresh token (Google).
    Lève ValueError si refusé — jamais de secret dans le message."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        raise ValueError(
            f"Échange du code OAuth refusé par Google (HTTP {resp.status_code})"
        )
    data = resp.json()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise ValueError(
            "Google n'a pas renvoyé de refresh token — reconnecte-toi via /connect"
        )
    return refresh_token


def save_refresh_token(db: Session, user_id: int, refresh_token: str) -> None:
    """Stocke (ou remplace) le token uniquement si l'utilisateur existe encore."""
    if db.get(User, user_id) is None:
        raise ValueError("Utilisateur introuvable")
    encrypted = encrypt_token(refresh_token)
    existing = db.execute(
        select(GmailConnection).where(GmailConnection.user_id == user_id)
    ).scalar_one_or_none()
    if existing:
        existing.encrypted_refresh_token = encrypted
        existing.status = "connected"
        existing.revocation_notified_at = None
        db.commit()
        return
    db.add(GmailConnection(user_id=user_id, encrypted_refresh_token=encrypted))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError("Connexion Gmail déjà enregistrée pour cet utilisateur")


def revoke_company_gmail(
    db: Session,
    *,
    company_id: int,
) -> bool:
    """Révoque Gmail sans prétendre qu'un avis Telegram a été envoyé."""
    company_user_id = (
        select(Company.user_id).where(Company.id == company_id).scalar_subquery()
    )
    result = db.execute(
        update(GmailConnection)
        .where(
            GmailConnection.user_id == company_user_id,
            GmailConnection.status == "connected",
        )
        .values(status="revoked", updated_at=func.now())
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    db.commit()
    return True


def mark_company_gmail_revocation_notified(
    db: Session,
    *,
    company_id: int,
) -> bool:
    """Marque l'avis seulement après la confirmation de son envoi."""
    company_user_id = (
        select(Company.user_id).where(Company.id == company_id).scalar_subquery()
    )
    result = db.execute(
        update(GmailConnection)
        .where(
            GmailConnection.user_id == company_user_id,
            GmailConnection.status == "revoked",
            GmailConnection.revocation_notified_at.is_(None),
        )
        .values(revocation_notified_at=func.now(), updated_at=func.now())
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    db.commit()
    return True


def get_connection(db: Session, user_id: int) -> GmailConnection | None:
    return db.execute(
        select(GmailConnection).where(GmailConnection.user_id == user_id)
    ).scalar_one_or_none()


def is_connected(db: Session, user_id: int) -> bool:
    connection = get_connection(db, user_id)
    return connection is not None and connection.status == "connected"
