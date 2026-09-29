"""
SmartReply Agent — Gmail OAuth : chiffrement Fernet + state signé (Étape 3)
==========================================================================
- Refresh tokens : chiffrés (Fernet) avec la clé TOKEN_ENCRYPTION_KEY
  (clé Fernet base64-32octets, ou passphrase dérivée en SHA-256).
- State OAuth : id utilisateur + expiration, signé HMAC avec une clé dérivée
  de JWT_SECRET — protège le callback contre le CSRF, sans stockage, sans
  aucune donnée sensible dans les URLs.
"""
import base64
import hashlib
import hmac
import time
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

# Durée de vie du state OAuth : le temps de passer par l'écran de consentement
STATE_TTL_SECONDS = 600


def _fernet() -> Fernet:
    if not settings.TOKEN_ENCRYPTION_KEY:
        raise RuntimeError(
            "TOKEN_ENCRYPTION_KEY manquante : définissez cette variable d'environnement (Render)."
        )
    key = settings.TOKEN_ENCRYPTION_KEY.strip()
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        # Passphrase libre → clé Fernet dérivée (SHA-256)
        digest = hashlib.sha256(key.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(plain: str) -> str:
    """Chiffre un refresh token pour le stockage en base."""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Déchiffre un refresh token stocké (usage interne uniquement, jamais exposé)."""
    try:
        return _fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise RuntimeError(
            "Impossible de déchiffrer le token stocké (TOKEN_ENCRYPTION_KEY modifiée ?)"
        )


def _state_key() -> bytes:
    return hashlib.sha256(
        (settings.JWT_SECRET + "|smartreply-gmail-oauth-state").encode()
    ).digest()


def sign_state(user_id: int) -> str:
    """State OAuth = id utilisateur + expiration, signé HMAC (anti-CSRF)."""
    exp = int(time.time()) + STATE_TTL_SECONDS
    payload = f"{user_id}.{exp}".encode()
    sig = hmac.new(_state_key(), payload, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(payload).decode()
        + "."
        + base64.urlsafe_b64encode(sig).decode()
    )


def verify_state(state: str) -> Optional[int]:
    """Retourne l'id utilisateur si le state est authentique et frais, sinon None."""
    try:
        payload_b64, sig_b64 = state.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_b64)
        sig = base64.urlsafe_b64decode(sig_b64)
        expected = hmac.new(_state_key(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        user_id_str, exp_str = payload.decode().split(".", 1)
        if int(exp_str) < time.time():
            return None
        return int(user_id_str)
    except Exception:
        return None
