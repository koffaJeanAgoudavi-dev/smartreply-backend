"""Statut opérationnel dérivé de la suspension temporaire d'une entreprise."""

from datetime import UTC, datetime
from typing import Protocol


class CompanySuspensionFields(Protocol):
    suspended_at: datetime | None
    suspended_until: datetime | None
    suspension_reason: str | None


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_company_suspended(
    company: CompanySuspensionFields,
    *,
    now: datetime | None = None,
) -> bool:
    """Une suspension sans fin, ou dont la fin est future, reste active."""

    if company.suspended_at is None:
        return False
    if company.suspended_until is None:
        return True
    current_time = as_utc(now or datetime.now(UTC))
    return as_utc(company.suspended_until) > current_time
