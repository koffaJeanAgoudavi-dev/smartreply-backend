"""Réservation atomique et transitions sûres du registre de dédoublonnage."""

import re
from dataclasses import dataclass, field

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.processed_emails.models import ProcessedEmail

MAX_PROCESSING_ATTEMPTS = 3
_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


@dataclass(frozen=True)
class EmailReservation:
    """Réservation interne d'un message, sans contenu ni adresse."""

    record_id: int
    company_id: int
    gmail_message_id: str = field(repr=False)
    attempt: int

    @property
    def sheet_email_id(self) -> str:
        """Identifiant stable et unique dans le Sheet de cette entreprise."""
        return f"EM-{self.record_id}"


def _validated_error_code(code: str) -> str:
    normalized = code.strip().lower()
    if not _SAFE_ERROR_CODE.fullmatch(normalized):
        return "internal_error"
    return normalized


def _reservation_from_record(record: ProcessedEmail) -> EmailReservation:
    return EmailReservation(
        record_id=record.id,
        company_id=record.company_id,
        gmail_message_id=record.gmail_message_id,
        attempt=record.attempts,
    )


def reserve_email(
    db: Session,
    *,
    company_id: int,
    gmail_message_id: str,
) -> EmailReservation | None:
    """Réserve un message une fois, ou reprend un échec sous la limite de trois."""
    record = ProcessedEmail(
        company_id=company_id,
        gmail_message_id=gmail_message_id,
        status="processing",
        attempts=1,
        last_error_code=None,
    )
    db.add(record)
    try:
        db.flush()
        reservation = _reservation_from_record(record)
        db.commit()
        return reservation
    except IntegrityError:
        # Une autre transaction a gagné l'insertion UNIQUE.
        db.rollback()

    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.gmail_message_id == gmail_message_id,
            ProcessedEmail.status == "failed",
            ProcessedEmail.attempts < MAX_PROCESSING_ATTEMPTS,
        )
        .values(
            status="processing",
            attempts=ProcessedEmail.attempts + 1,
            last_error_code=None,
            updated_at=func.now(),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    resumed = db.execute(
        select(ProcessedEmail).where(
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.gmail_message_id == gmail_message_id,
        )
    ).scalar_one()
    return _reservation_from_record(resumed)


def _set_status(
    db: Session,
    reservation: EmailReservation,
    *,
    status: str,
    last_error_code: str | None,
) -> None:
    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.id == reservation.record_id,
            ProcessedEmail.company_id == reservation.company_id,
            ProcessedEmail.gmail_message_id == reservation.gmail_message_id,
            ProcessedEmail.status == "processing",
        )
        .values(
            status=status,
            last_error_code=last_error_code,
            updated_at=func.now(),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise RuntimeError("processed_email_state_conflict")
    db.commit()


def mark_done(db: Session, reservation: EmailReservation) -> None:
    _set_status(db, reservation, status="done", last_error_code=None)


def mark_skipped(db: Session, reservation: EmailReservation) -> None:
    _set_status(db, reservation, status="skipped", last_error_code=None)


def mark_failed(
    db: Session,
    reservation: EmailReservation,
    *,
    error_code: str,
) -> None:
    _set_status(
        db,
        reservation,
        status="failed",
        last_error_code=_validated_error_code(error_code),
    )


def release_after_quota(
    db: Session,
    reservation: EmailReservation,
    *,
    error_code: str = "quota_429",
) -> None:
    """Annule le coût de la tentative 429 puis rend le message réessayable."""
    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.id == reservation.record_id,
            ProcessedEmail.company_id == reservation.company_id,
            ProcessedEmail.gmail_message_id == reservation.gmail_message_id,
            ProcessedEmail.status == "processing",
        )
        .values(
            status="failed",
            attempts=case(
                (ProcessedEmail.attempts > 0, ProcessedEmail.attempts - 1),
                else_=0,
            ),
            last_error_code=_validated_error_code(error_code),
            updated_at=func.now(),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise RuntimeError("processed_email_state_conflict")
    db.commit()
