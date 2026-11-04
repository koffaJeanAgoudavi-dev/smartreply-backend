"""Source unique de l'allowlist et du statut de validation des entreprises."""

from typing import Literal

from app.core.config import settings

CompanyValidationStatus = Literal["pending", "validated"]


def parse_allowed_company_ids(raw_value: str | None = None) -> frozenset[int]:
    """Parse la liste CSV en mode fail-closed ; vide signifie aucune validation."""
    raw = settings.PIPELINE_ALLOWED_COMPANY_IDS if raw_value is None else raw_value
    if not raw.strip():
        return frozenset()

    company_ids: set[int] = set()
    for value in raw.split(","):
        candidate = value.strip()
        if not candidate.isdecimal() or int(candidate) <= 0:
            raise ValueError("PIPELINE_ALLOWED_COMPANY_IDS invalide")
        company_ids.add(int(candidate))
    return frozenset(company_ids)


def get_company_validation_status(
    company_id: int,
    raw_value: str | None = None,
) -> CompanyValidationStatus:
    """Dérive le statut affiché de la même allowlist que le pipeline réel."""
    return (
        "validated"
        if company_id in parse_allowed_company_ids(raw_value)
        else "pending"
    )
