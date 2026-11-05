"""Autorisation serveur de la console d'administration."""

from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.core.admin_allowlist import parse_admin_user_ids
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User


def get_current_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Refuse tout utilisateur absent de l'allowlist administrateur."""

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
