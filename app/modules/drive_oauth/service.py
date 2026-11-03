"""OAuth Google Drive par utilisateur avec stockage chiffré du refresh token."""

import logging
import urllib.parse

import httpx
from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.auth.models import User
from app.modules.drive_oauth.models import DriveConnection
from app.modules.gmail_oauth.security import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


class DriveOAuthConfigurationError(RuntimeError):
    """Le client OAuth Drive ou son URL de retour ne sont pas configurés."""


class DriveAuthorizationRevokedError(RuntimeError):
    """Le consentement Drive stocké n'est plus utilisable."""


class DriveConnectionRequiredError(RuntimeError):
    """L'utilisateur doit connecter Drive avant de créer son entreprise."""


def _require_client_configuration() -> None:
    if not settings.GOOGLE_DRIVE_CLIENT_ID or not settings.GOOGLE_DRIVE_CLIENT_SECRET:
        raise DriveOAuthConfigurationError(
            "Connexion Google Drive temporairement indisponible."
        )


def redirect_uri() -> str:
    """Construit l'URI de callback Drive depuis la seule BASE_URL autorisée."""
    if not settings.BASE_URL:
        raise DriveOAuthConfigurationError(
            "BASE_URL manquant : définissez l'URL publique du service."
        )
    base_url = settings.BASE_URL.rstrip("/")
    parsed = urllib.parse.urlsplit(base_url)
    if settings.ENVIRONMENT.strip().lower() == "production" and (
        parsed.scheme != "https" or not parsed.netloc
    ):
        raise DriveOAuthConfigurationError(
            "BASE_URL doit être une URL HTTPS en production."
        )
    return base_url + "/api/v1/drive/callback"


def build_authorization_url(state: str) -> str:
    """Demande uniquement ``drive.file`` et un refresh token hors ligne."""
    _require_client_configuration()
    params = {
        "client_id": settings.GOOGLE_DRIVE_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": DRIVE_FILE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "false",
        "state": state,
    }
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)


async def exchange_code_for_refresh_token(code: str) -> str:
    """Échange le code sans jamais exposer la réponse Google ni ses secrets."""
    _require_client_configuration()
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_DRIVE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_DRIVE_CLIENT_SECRET,
                    "redirect_uri": redirect_uri(),
                    "grant_type": "authorization_code",
                },
            )
        except httpx.HTTPError as exc:
            logger.error("Échange OAuth Drive interrompu (%s)", type(exc).__name__)
            raise ValueError(
                "Google Drive est temporairement inaccessible."
            ) from exc

    if response.status_code != 200:
        logger.warning("Échange OAuth Drive refusé (HTTP %s)", response.status_code)
        raise ValueError("Échange du code OAuth Drive refusé par Google.")
    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError("Réponse OAuth Drive invalide.") from exc
    if not isinstance(data, dict) or not data.get("refresh_token"):
        raise ValueError(
            "Google n'a pas renvoyé de refresh token Drive. Recommencez la connexion."
        )
    return str(data["refresh_token"])


def save_refresh_token(db: Session, user_id: int, refresh_token: str) -> None:
    """Crée ou remplace atomiquement le token Drive chiffré de l'utilisateur."""
    if db.get(User, user_id) is None:
        raise ValueError("Utilisateur introuvable")
    encrypted = encrypt_token(refresh_token)
    existing = get_connection(db, user_id)
    if existing is not None:
        existing.encrypted_refresh_token = encrypted
        existing.status = "connected"
        db.commit()
        return

    db.add(
        DriveConnection(
            user_id=user_id,
            encrypted_refresh_token=encrypted,
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError(
            "Connexion Google Drive déjà enregistrée pour cet utilisateur."
        ) from exc


def get_connection(db: Session, user_id: int) -> DriveConnection | None:
    return db.execute(
        select(DriveConnection).where(DriveConnection.user_id == user_id)
    ).scalar_one_or_none()


def get_user_access_token(db: Session, user_id: int) -> str:
    """Rafraîchit en mémoire l'accès Drive d'un utilisateur connecté."""
    connection = get_connection(db, user_id)
    if connection is None:
        raise DriveConnectionRequiredError(
            "Connectez Google Drive avant de créer votre espace."
        )
    if connection.status != "connected":
        raise DriveAuthorizationRevokedError(
            "La connexion Google Drive doit être renouvelée."
        )
    _require_client_configuration()

    try:
        refresh_token = decrypt_token(connection.encrypted_refresh_token)
    except RuntimeError as exc:
        logger.error("Déchiffrement du token Drive impossible")
        raise DriveOAuthConfigurationError(
            "Connexion Google Drive temporairement indisponible."
        ) from exc

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=GOOGLE_TOKEN_URL,
        client_id=settings.GOOGLE_DRIVE_CLIENT_ID,
        client_secret=settings.GOOGLE_DRIVE_CLIENT_SECRET,
        scopes=[DRIVE_FILE_SCOPE],
    )
    try:
        credentials.refresh(Request())
    except RefreshError as exc:
        if getattr(exc, "retryable", False):
            logger.error("Rafraîchissement OAuth Drive temporairement indisponible")
            raise DriveOAuthConfigurationError(
                "Authentification Google Drive temporairement indisponible."
            ) from exc
        logger.warning("Autorisation OAuth Drive révoquée")
        connection.status = "revoked"
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.error("Persistance de la révocation Drive impossible")
        raise DriveAuthorizationRevokedError(
            "La connexion Google Drive doit être renouvelée."
        ) from exc
    except GoogleAuthError as exc:
        logger.error("Rafraîchissement OAuth Drive impossible (%s)", type(exc).__name__)
        raise DriveOAuthConfigurationError(
            "Authentification Google Drive temporairement indisponible."
        ) from exc

    if not credentials.token:
        raise DriveOAuthConfigurationError(
            "Google OAuth n'a renvoyé aucun jeton d'accès Drive."
        )
    return credentials.token


def revoke_refresh_token(encrypted_refresh_token: str) -> bool:
    """Révoque au mieux le grant Google ; False impose une vérification manuelle."""
    try:
        refresh_token = decrypt_token(encrypted_refresh_token)
    except RuntimeError:
        logger.error("Token Drive indéchiffrable pendant la déconnexion")
        return False

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                GOOGLE_REVOKE_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={"token": refresh_token},
            )
    except httpx.HTTPError as exc:
        logger.warning("Révocation OAuth Drive interrompue (%s)", type(exc).__name__)
        return False
    return response.status_code == 200


def delete_connection(db: Session, connection: DriveConnection) -> None:
    """Supprime le secret local après arrêt de l'accès pipeline."""
    db.delete(connection)
    db.commit()
