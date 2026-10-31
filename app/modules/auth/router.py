"""
SmartReply Agent — MODULE AUTH : inscription + connexion (Étape 2)
==================================================================
Première brique de la couche multi-utilisateur : comptes email + mot de passe
dans la base Neon (table users), tokens JWT (HS256, validité 24 h).

Endpoints :
  POST /api/v1/auth/register  — crée un compte, renvoie l'utilisateur + un JWT
  POST /api/v1/auth/login     — vérifie les identifiants, renvoie un JWT

Tests :
  curl -X POST http://localhost:8000/api/v1/auth/register \
    -H "Content-Type: application/json" \
    -d '{"email": "alice@example.com", "password": "motdepasse123", \
         "invitation_code": "votre-code"}'

  curl -X POST http://localhost:8000/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email": "alice@example.com", "password": "motdepasse123"}'
"""

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.client_ip import ClientAddressError, get_client_ip
from app.core.config import settings
from app.core.rate_limit import (
    RedisRateLimiter,
    enforce_rate_limit,
    get_rate_limiter,
)
from app.db.database import get_db
from app.modules.auth.schemas import (
    RegisterResponse,
    Token,
    UserCreate,
    UserLogin,
    UserOut,
)
from app.modules.auth.security import create_access_token
from app.modules.auth.service import authenticate_user, create_user, normalize_email

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


def _normalize_invitation_code(code: str) -> bytes:
    """Retire espaces et tirets, puis passe le code en majuscules."""
    return "".join(code.split()).replace("-", "").upper().encode("utf-8")


def _invitation_code_is_valid(invitation_code: str | None) -> bool:
    """Compare le candidat normalisé à chaque code, sans sortie anticipée."""
    normalized_codes = (
        _normalize_invitation_code(code)
        for code in settings.INVITATION_CODES.split(",")
    )
    allowed_codes = tuple(code for code in normalized_codes if code)
    candidate = _normalize_invitation_code(invitation_code or "")
    is_valid = False
    for allowed_code in allowed_codes:
        is_valid |= hmac.compare_digest(candidate, allowed_code)
    return is_valid


def _rate_limit_client_ip(
    request: Request,
    *,
    required: bool,
) -> str | None:
    try:
        return get_client_ip(request)
    except ClientAddressError as exc:
        if not required:
            # Une IP douteuse ne doit jamais entrer dans une clé de confiance.
            # La limite login par email reste alors la protection fail-safe.
            return None
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Protection anti-abus temporairement indisponible",
        ) from exc


@router.post(
    "/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED
)
def register(
    payload: UserCreate,
    request: Request,
    limiter: RedisRateLimiter = Depends(get_rate_limiter),
    db: Session = Depends(get_db),
):
    """Crée un compte uniquement avec un code d'invitation autorisé."""
    client_ip = _rate_limit_client_ip(request, required=True)
    assert client_ip is not None
    enforce_rate_limit(limiter, limiter.register_buckets(client_ip))

    if not _invitation_code_is_valid(payload.invitation_code):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Code d'invitation invalide",
        )

    try:
        user = create_user(db, payload.email, payload.password)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Un compte existe déjà pour cet email",
        )
    return RegisterResponse(
        user=UserOut.model_validate(user),
        access_token=create_access_token(user.id, user.email),
    )


@router.post("/login", response_model=Token)
def login(
    payload: UserLogin,
    request: Request,
    limiter: RedisRateLimiter = Depends(get_rate_limiter),
    db: Session = Depends(get_db),
):
    """Connexion : identifiants valides → JWT (HS256, validité 24 h)."""
    client_ip = _rate_limit_client_ip(request, required=False)
    enforce_rate_limit(
        limiter,
        limiter.login_buckets(client_ip, normalize_email(payload.email)),
    )

    user = authenticate_user(db, payload.email, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect",
        )
    return Token(access_token=create_access_token(user.id, user.email))
