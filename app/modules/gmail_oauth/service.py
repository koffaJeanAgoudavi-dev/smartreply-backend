"""
SmartReply Agent — Gmail OAuth : logique métier (Étape 3)
========================================================
Liaison utilisateur ↔ Gmail : URL de consentement (scope gmail.modify UNIQUEMENT),
échange code → refresh token, stockage chiffré dans la table gmail_connections.
Le refresh token n'apparaît jamais dans une réponse API ni dans un log.
"""
import urllib.parse

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.gmail_oauth.models import GmailConnection
from app.modules.gmail_oauth.security import encrypt_token

# Scope unique demandé — aucun droit au-delà de la modification Gmail
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def redirect_uri() -> str:
    """URI de redirection enregistrée dans le client OAuth Google (dérivée de BASE_URL)."""
    if not settings.BASE_URL:
        raise RuntimeError(
            "BASE_URL manquant : définissez l'URL publique du service (Render)."
        )
    return settings.BASE_URL.rstrip("/") + "/api/v1/gmail/callback"


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
        raise ValueError(f"Échange du code OAuth refusé par Google (HTTP {resp.status_code})")
    data = resp.json()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise ValueError(
            "Google n'a pas renvoyé de refresh token — reconnecte-toi via /connect"
        )
    return refresh_token


def save_refresh_token(db: Session, user_id: int, refresh_token: str) -> None:
    """Stocke (ou remplace) le refresh token CHIFFRÉ de l'utilisateur."""
    encrypted = encrypt_token(refresh_token)
    existing = db.execute(
        select(GmailConnection).where(GmailConnection.user_id == user_id)
    ).scalar_one_or_none()
    if existing:
        existing.encrypted_refresh_token = encrypted
        db.commit()
        return
    db.add(GmailConnection(user_id=user_id, encrypted_refresh_token=encrypted))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError("Connexion Gmail déjà enregistrée pour cet utilisateur")


def get_connection(db: Session, user_id: int) -> GmailConnection | None:
    return db.execute(
        select(GmailConnection).where(GmailConnection.user_id == user_id)
    ).scalar_one_or_none()


def is_connected(db: Session, user_id: int) -> bool:
    return get_connection(db, user_id) is not None
