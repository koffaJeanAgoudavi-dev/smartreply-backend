"""
SmartReply Agent — GMAIL OAUTH : connexion du Gmail de l'utilisateur (Étape 3)
==============================================================================
Permet à chaque utilisateur (compte JWT existant) de connecter SON Gmail
(scope gmail.modify uniquement) et stocke son refresh token CHIFFRÉ dans la
base Neon (table gmail_connections) — jamais dans un Google Sheet.

Endpoints :
  GET /api/v1/gmail/connect   (JWT)  → URL de consentement Google à ouvrir
  GET /api/v1/gmail/callback  (redirection Google, protégée par state signé)
                              → lie le Gmail à l'utilisateur, stocke le token
  GET /api/v1/gmail/status    (JWT)  → {"connected": true|false}

Sécurité : le refresh token n'apparaît JAMAIS dans une réponse API, un log,
ou côté client ; il est chiffré au stockage (Fernet / TOKEN_ENCRYPTION_KEY).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.gmail_oauth import service
from app.modules.gmail_oauth.schemas import ConnectResponse, StatusResponse
from app.modules.gmail_oauth.security import sign_state, verify_state

router = APIRouter(prefix="/api/v1/gmail", tags=["Gmail OAuth"])


@router.get("/connect", response_model=ConnectResponse)
def connect(current_user: User = Depends(get_current_user)):
    """Génère l'URL d'autorisation Google de l'utilisateur (scope gmail.modify)."""
    try:
        url = service.build_authorization_url(sign_state(current_user.id))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ConnectResponse(authorization_url=url)


@router.get("/callback", response_class=RedirectResponse, status_code=303)
async def callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """Traite le retour Google et stocke le refresh token pour le state signé."""
    user_id = verify_state(state) if state else None
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "State OAuth invalide ou expiré — recommence depuis "
                "/api/v1/gmail/connect"
            ),
        )

    # Le state prouve l'intention initiale, pas l'existence actuelle du compte.
    # Ce rechargement précède tout échange Google et empêche une révocation JWT
    # différée de créer une connexion Gmail pour un utilisateur supprimé.
    if db.get(User, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur introuvable",
        )

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Consentement refusé par Google ({error})",
        )
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Code d'autorisation manquant",
        )
    try:
        refresh_token = await service.exchange_code_for_refresh_token(code)
        service.save_refresh_token(db, user_id, refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        url="/dashboard/?gmail=connected#integrations",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/status", response_model=StatusResponse)
def gmail_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Statut de connexion Gmail de l'utilisateur : connecté ou non."""
    return StatusResponse(connected=service.is_connected(db, current_user.id))
