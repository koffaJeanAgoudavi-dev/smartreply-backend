"""State OAuth Drive signé avec une audience distincte de Gmail."""

import base64
import hashlib
import hmac
import time

from app.core.config import settings

STATE_TTL_SECONDS = 600


def _state_key() -> bytes:
    return hashlib.sha256(
        (settings.JWT_SECRET + "|smartreply-drive-oauth-state").encode()
    ).digest()


def sign_state(user_id: int) -> str:
    """Signe l'utilisateur et une expiration sans exposer de secret."""
    expiration = int(time.time()) + STATE_TTL_SECONDS
    payload = f"{user_id}.{expiration}".encode()
    signature = hmac.new(_state_key(), payload, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(payload).decode()
        + "."
        + base64.urlsafe_b64encode(signature).decode()
    )


def verify_state(state: str) -> int | None:
    """Retourne l'utilisateur uniquement pour un state Drive authentique et frais."""
    try:
        payload_b64, signature_b64 = state.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_b64)
        signature = base64.urlsafe_b64decode(signature_b64)
        expected = hmac.new(_state_key(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            return None
        user_id_raw, expiration_raw = payload.decode().split(".", 1)
        if int(expiration_raw) < time.time():
            return None
        return int(user_id_raw)
    except Exception:
        return None
