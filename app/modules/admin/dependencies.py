"""Autorisation serveur à deux niveaux de la console d'administration."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.admin_allowlist import parse_admin_user_ids
from app.modules.admin.security import (
    ADMIN_SESSION_COOKIE,
    AdminSecurityConfigurationError,
    AdminSession,
    InvalidAdminSessionError,
    decode_admin_session_token,
)
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User


def get_allowlisted_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Exige le JWT utilisateur et l'appartenance à l'allowlist administrateur."""

    try:
        allowed_user_ids = parse_admin_user_ids()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configuration administrateur indisponible",
        ) from exc

    if current_user.id not in allowed_user_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès administrateur refusé",
        )
    return current_user


def optional_admin_session(
    request: Request,
    current_admin: User,
) -> AdminSession | None:
    """Retourne la session secondaire valide, sans ouvrir l'accès si elle manque."""

    token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    try:
        return decode_admin_session_token(token, current_admin.id)
    except AdminSecurityConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configuration du code administrateur indisponible",
        ) from exc
    except InvalidAdminSessionError:
        return None


def get_current_admin(
    request: Request,
    current_admin: Annotated[User, Depends(get_allowlisted_admin)],
) -> User:
    """Exige en plus une session de code administrateur valide de 30 minutes."""

    if optional_admin_session(request, current_admin) is None:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Code administrateur requis",
        )
    return current_admin
