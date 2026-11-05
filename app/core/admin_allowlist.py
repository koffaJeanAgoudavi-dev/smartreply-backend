"""Allowlist fail-closed des utilisateurs autorisés à administrer SmartReply."""

from app.core.config import settings


def parse_admin_user_ids(raw_value: str | None = None) -> frozenset[int]:
    """Parse une liste CSV d'identifiants utilisateur strictement positifs."""

    raw = settings.ADMIN_ALLOWED_USER_IDS if raw_value is None else raw_value
    if not raw.strip():
        return frozenset()

    user_ids: set[int] = set()
    for value in raw.split(","):
        candidate = value.strip()
        if not candidate.isdecimal() or int(candidate) <= 0:
            raise ValueError("ADMIN_ALLOWED_USER_IDS invalide")
        user_ids.add(int(candidate))
    return frozenset(user_ids)
