"""Code secondaire et session courte de la console administrateur."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.config import settings

ADMIN_SESSION_COOKIE = "smartreply_admin_session"
ADMIN_SESSION_DURATION_SECONDS = 30 * 60
_ADMIN_SESSION_ALGORITHM = "HS256"
_ADMIN_SESSION_ISSUER = "smartreply"
_ADMIN_SESSION_AUDIENCE = "smartreply-admin"


class AdminSecurityConfigurationError(RuntimeError):
    """La configuration ne permet pas de déverrouiller l'administration."""


class InvalidAdminSessionError(RuntimeError):
    """Le cookie ne constitue pas une session administrateur valide."""


@dataclass(frozen=True)
class AdminSession:
    user_id: int
    expires_at: datetime


def _configured_access_code() -> str:
    code = settings.ADMIN_ACCESS_CODE.strip()
    if len(code) != 8:
        raise AdminSecurityConfigurationError(
            "ADMIN_ACCESS_CODE doit contenir exactement 8 caractères"
        )
    return code


def _signing_secret() -> str:
    secret = settings.JWT_SECRET.strip()
    if not secret:
        raise AdminSecurityConfigurationError("JWT_SECRET manquant")
    return secret


def _code_version(code: str, signing_secret: str) -> str:
    """Lie les sessions au code actuel sans placer son empreinte brute dans le JWT."""

    return hmac.new(
        signing_secret.encode("utf-8"),
        f"admin-code\0{code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_admin_access_code(candidate: str) -> bool:
    """Compare le code candidat au secret Render sans sortie anticipée."""

    configured = _configured_access_code()
    return hmac.compare_digest(
        candidate.strip().encode("utf-8"),
        configured.encode("utf-8"),
    )


def create_admin_session_token(user_id: int) -> tuple[str, datetime]:
    """Émet une session admin signée, liée au compte et au code actuellement actif."""

    if user_id <= 0:
        raise ValueError("Identifiant administrateur invalide")
    code = _configured_access_code()
    signing_secret = _signing_secret()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ADMIN_SESSION_DURATION_SECONDS)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "admin_session",
        "code_version": _code_version(code, signing_secret),
        "jti": secrets.token_urlsafe(18),
        "iat": now,
        "exp": expires_at,
        "iss": _ADMIN_SESSION_ISSUER,
        "aud": _ADMIN_SESSION_AUDIENCE,
    }
    token = jwt.encode(payload, signing_secret, algorithm=_ADMIN_SESSION_ALGORITHM)
    return token, expires_at


def decode_admin_session_token(token: str, expected_user_id: int) -> AdminSession:
    """Valide signature, durée, compte, audience et rotation du code admin."""

    if expected_user_id <= 0:
        raise InvalidAdminSessionError("Session administrateur invalide")
    try:
        code = _configured_access_code()
        signing_secret = _signing_secret()
        if not token:
            raise InvalidAdminSessionError("Session administrateur absente")
        payload = jwt.decode(
            token,
            signing_secret,
            algorithms=[_ADMIN_SESSION_ALGORITHM],
            audience=_ADMIN_SESSION_AUDIENCE,
            issuer=_ADMIN_SESSION_ISSUER,
            options={
                "require": [
                    "sub",
                    "type",
                    "code_version",
                    "jti",
                    "iat",
                    "exp",
                    "iss",
                    "aud",
                ]
            },
        )
        user_id = int(payload["sub"])
        expires_at = datetime.fromtimestamp(float(payload["exp"]), timezone.utc)
        token_type = payload["type"]
        token_code_version = payload["code_version"]
    except AdminSecurityConfigurationError:
        raise
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise InvalidAdminSessionError("Session administrateur invalide") from exc

    expected_code_version = _code_version(code, signing_secret)
    if (
        user_id != expected_user_id
        or token_type != "admin_session"
        or not isinstance(token_code_version, str)
        or not hmac.compare_digest(token_code_version, expected_code_version)
    ):
        raise InvalidAdminSessionError("Session administrateur invalide")
    return AdminSession(user_id=user_id, expires_at=expires_at)
