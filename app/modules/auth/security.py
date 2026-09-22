"""
SmartReply Agent — Auth : hachage Argon2 + JWT (Étape 2)
========================================================
- Mots de passe : Argon2id (argon2-cffi), stockés uniquement hachés.
- Tokens : JWT HS256 signés avec settings.JWT_SECRET (variable d'environnement
  à créer sur Render), validité 24 h (settings.ACCESS_TOKEN_EXPIRE_MINUTES).
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import settings

ALGORITHM = "HS256"

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Retourne l'empreinte Argon2 du mot de passe (→ users.hashed_password)."""
    return _hasher.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Vérifie un mot de passe en clair contre une empreinte Argon2."""
    try:
        _hasher.verify(hashed_password, password)
        return True
    except (InvalidHashError, VerificationError):
        return False


def create_access_token(user_id: int, email: str) -> str:
    """Émet un JWT d'accès (sub = id utilisateur) valable 24 h."""
    if not settings.JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET manquant : définissez cette variable d'environnement (Render)."
        )
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Décode et vérifie (signature + expiration) un JWT. Lève jwt.PyJWTError si invalide."""
    if not settings.JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET manquant : définissez cette variable d'environnement (Render)."
        )
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
