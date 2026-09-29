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
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.modules.auth.security import decode_access_token
from app.modules.gmail_oauth import service
from app.modules.gmail_oauth.schemas import CallbackResponse, ConnectResponse, StatusResponse
from app.modules.gmail_oauth.security import sign_state, verify_state

router = APIRouter(prefix="/api/v1/gmail", tags=["Gmail OAuth"])


def get_current_user_id(authorization: str = Header(default="")) -> int:
    """Extrait l'utilisateur depuis son JWT (header Authorization: Bearer ...)."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token manquant (Authorization: Bearer ...)")
    try:
        payload = decode_access_token(authorization.removeprefix("Bearer ").strip())
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré")
    try:
        return int(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Token invalide")


@router.get("/connect", response_model=ConnectResponse)
def connect(user_id: int = Depends(get_current_user_id)):
    """Génère l'URL d'autorisation Google de l'utilisateur (scope gmail.modify)."""
    try:
        url = service.build_authorization_url(sign_state(user_id))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return ConnectResponse(authorization_url=url)


@router.get("/callback", response_model=CallbackResponse)
async def callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """Appelé par Google après consentement : vérifie le state (anti-CSRF), échange le
    code contre un refresh token et le stocke chiffré pour l'utilisateur lié au state."""
    user_id = verify_state(state) if state else None
    if user_id is None:
        raise HTTPException(
            status_code=400,
            detail="State OAuth invalide ou expiré — recommence depuis /api/v1/gmail/connect",
        )
    if error:
        raise HTTPException(status_code=400, detail=f"Consentement refusé par Google ({error})")
    if not code:
        raise HTTPException(status_code=400, detail="Code d'autorisation manquant")
    try:
        refresh_token = await service.exchange_code_for_refresh_token(code)
        service.save_refresh_token(db, user_id, refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return CallbackResponse(message="Gmail connecté avec succès")


@router.get("/status", response_model=StatusResponse)
def gmail_status(user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    """Statut de connexion Gmail de l'utilisateur : connecté ou non."""
    return StatusResponse(connected=service.is_connected(db, user_id))
