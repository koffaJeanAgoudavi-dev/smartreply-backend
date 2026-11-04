"""Endpoints JWT de la connexion Google Drive détenue par le client."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.companies import service as companies_service
from app.modules.drive_oauth import service
from app.modules.drive_oauth.schemas import (
    DriveConnectResponse,
    DriveDisconnectResponse,
    DriveStatusResponse,
)
from app.modules.drive_oauth.security import sign_state, verify_state

router = APIRouter(prefix="/api/v1/drive", tags=["Google Drive OAuth"])


@router.get("/connect", response_model=DriveConnectResponse)
def connect(current_user: User = Depends(get_current_user)) -> DriveConnectResponse:
    """Retourne le consentement séparé et minimal ``drive.file``."""
    try:
        authorization_url = service.build_authorization_url(
            sign_state(current_user.id)
        )
    except service.DriveOAuthConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return DriveConnectResponse(authorization_url=authorization_url)


@router.get("/callback", response_class=RedirectResponse, status_code=303)
async def callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """Stocke le refresh token chiffré pour l'utilisateur du state signé."""
    user_id = verify_state(state) if state else None
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "State OAuth Drive invalide ou expiré — recommencez depuis "
                "/api/v1/drive/connect"
            ),
        )
    if db.get(User, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur introuvable",
        )
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Consentement Google Drive refusé.",
        )
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Code d'autorisation Google Drive manquant.",
        )

    try:
        refresh_token = await service.exchange_code_for_refresh_token(code)
        service.save_refresh_token(db, user_id, refresh_token)
    except service.DriveOAuthConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return RedirectResponse(
        url="/dashboard/?drive=connected",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/status", response_model=DriveStatusResponse)
def drive_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DriveStatusResponse:
    """Inspecte la propriété, l'existence, la structure et l'accès pipeline."""
    return DriveStatusResponse(
        **companies_service.get_drive_status(db, current_user.id)
    )


@router.delete("/connection", response_model=DriveDisconnectResponse)
def disconnect(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DriveDisconnectResponse:
    """Laisse le fichier au client mais retire l'accès technique SmartReply."""
    try:
        result = companies_service.disconnect_client_drive(db, current_user.id)
    except companies_service.DriveConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except companies_service.DriveOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except companies_service.CompanyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return DriveDisconnectResponse(**result)
