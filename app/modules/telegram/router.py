"""API JWT et webhook sécurisé de liaison Telegram."""

import hmac
from datetime import timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.modules.auth.models import User
from app.modules.auth.security import decode_access_token
from app.modules.telegram import service
from app.modules.telegram.schemas import (
    TelegramLinkCodeResponse,
    TelegramStatusResponse,
)

router = APIRouter(prefix="/api/v1/telegram", tags=["Telegram"])
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    db: Session = Depends(get_db),
) -> User:
    """Valide le JWT et recharge l'utilisateur depuis Neon."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token manquant (Authorization: Bearer ...)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur introuvable",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@router.post("/link-code", response_model=TelegramLinkCodeResponse)
def create_telegram_link_code(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TelegramLinkCodeResponse:
    """Crée un code SR-XXXXX à usage unique, valable quinze minutes."""
    try:
        link_code = service.create_link_code(db, current_user.id)
    except service.TelegramAlreadyConnectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except service.LinkCodeGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    expires_at = link_code.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return TelegramLinkCodeResponse(
        code=link_code.code,
        expires_at=expires_at,
        instruction=(
            f"Envoyez /start {link_code.code} au bot Telegram dans les 15 minutes."
        ),
    )


@router.get("/status", response_model=TelegramStatusResponse)
def get_telegram_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TelegramStatusResponse:
    """Retourne le statut sans jamais exposer les identifiants Telegram."""
    connected, connected_at = service.get_connection_status(db, current_user.id)
    return TelegramStatusResponse(
        connected=connected,
        connected_at=connected_at,
    )


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Reçoit les messages et callbacks Telegram après contrôle du secret."""
    configured_secret = settings.TELEGRAM_WEBHOOK_SECRET
    provided_secret = x_telegram_bot_api_secret_token or ""
    if not configured_secret or not hmac.compare_digest(
        provided_secret,
        configured_secret,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized webhook call",
        )

    update_payload = await request.json()
    await service.process_telegram_update(update_payload, db)
    return {"status": "ok"}
