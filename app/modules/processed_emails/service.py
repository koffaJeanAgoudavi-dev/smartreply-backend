"""Réservation atomique et transitions sûres du registre de dédoublonnage."""

import re
import secrets
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.dashboard.models import CompanyActivityEvent
from app.modules.processed_emails.models import ProcessedEmail

MAX_PROCESSING_ATTEMPTS = 3
_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


@dataclass(frozen=True)
class EmailReservation:
    """Réservation interne d'un message, sans contenu ni adresse."""

    record_id: int
    company_id: int
    gmail_message_id: str = field(repr=False)
    gmail_draft_id: str | None = field(repr=False)
    callback_token: str | None = field(repr=False)
    draft_status: str | None
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


def _add_activity_event(
    db: Session,
    *,
    company_id: int,
    record_id: int,
    event_type: str,
) -> None:
    """Ajoute l'événement dans la transaction qui a gagné la transition."""
    db.add(
        CompanyActivityEvent(
            company_id=company_id,
            processed_email_id=record_id,
            event_type=event_type,
        )
    )


def _reservation_from_record(record: ProcessedEmail) -> EmailReservation:
    return EmailReservation(
        record_id=record.id,
        company_id=record.company_id,
        gmail_message_id=record.gmail_message_id,
        gmail_draft_id=record.gmail_draft_id,
        callback_token=record.callback_token,
        draft_status=record.draft_status,
        attempt=record.attempts,
    )


def reserve_email(
    db: Session,
    *,
    company_id: int,
    gmail_message_id: str,
) -> EmailReservation | None:
    """Réserve un message ou reprend atomiquement un échec/traitement ancien."""
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

    stale_seconds = max(0, settings.PIPELINE_PROCESSING_STALE_SECONDS)
    stale_before = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.gmail_message_id == gmail_message_id,
            ProcessedEmail.attempts < MAX_PROCESSING_ATTEMPTS,
            or_(
                ProcessedEmail.status == "failed",
                and_(
                    ProcessedEmail.status == "processing",
                    ProcessedEmail.updated_at < stale_before,
                ),
            ),
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


def record_gmail_draft_id(
    db: Session,
    reservation: EmailReservation,
    *,
    gmail_draft_id: str,
) -> EmailReservation:
    """Persiste le brouillon et son jeton opaque avant toute écriture suivante."""
    normalized_id = gmail_draft_id.strip()
    if not normalized_id or len(normalized_id) > 255:
        raise ValueError("invalid_gmail_draft_id")
    if (
        reservation.gmail_draft_id is not None
        and reservation.gmail_draft_id != normalized_id
    ):
        raise RuntimeError("processed_email_draft_conflict")
    if reservation.callback_token is not None:
        return replace(
            reservation,
            gmail_draft_id=normalized_id,
            draft_status=reservation.draft_status or "pending",
        )

    # Une collision cryptographique est extraordinairement improbable, mais ne
    # doit jamais faire perdre l'ID Gmail déjà créé : la transaction est rejouée.
    for _ in range(5):
        callback_token = secrets.token_urlsafe(24)
        try:
            result = db.execute(
                update(ProcessedEmail)
                .where(
                    ProcessedEmail.id == reservation.record_id,
                    ProcessedEmail.company_id == reservation.company_id,
                    ProcessedEmail.gmail_message_id == reservation.gmail_message_id,
                    ProcessedEmail.status == "processing",
                    ProcessedEmail.attempts == reservation.attempt,
                    or_(
                        ProcessedEmail.gmail_draft_id.is_(None),
                        ProcessedEmail.gmail_draft_id == normalized_id,
                    ),
                    ProcessedEmail.callback_token.is_(None),
                )
                .values(
                    gmail_draft_id=normalized_id,
                    callback_token=callback_token,
                    draft_status="pending",
                    updated_at=func.now(),
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise RuntimeError("processed_email_state_conflict")
            _add_activity_event(
                db,
                company_id=reservation.company_id,
                record_id=reservation.record_id,
                event_type="draft_created",
            )
            db.commit()
            return replace(
                reservation,
                gmail_draft_id=normalized_id,
                callback_token=callback_token,
                draft_status="pending",
            )
        except IntegrityError:
            db.rollback()

    raise RuntimeError("processed_email_callback_token_collision")


def _set_status(
    db: Session,
    reservation: EmailReservation,
    *,
    status: str,
    last_error_code: str | None,
    category: str | None = None,
    priority: str | None = None,
    received_at: datetime | None = None,
    event_type: str | None = None,
) -> None:
    values = {
        "status": status,
        "last_error_code": last_error_code,
        "updated_at": func.now(),
    }
    if event_type is not None:
        values.update(
            category=category,
            priority=priority,
            received_at=received_at,
        )
    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.id == reservation.record_id,
            ProcessedEmail.company_id == reservation.company_id,
            ProcessedEmail.gmail_message_id == reservation.gmail_message_id,
            ProcessedEmail.status == "processing",
            ProcessedEmail.attempts == reservation.attempt,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        db.rollback()
        raise RuntimeError("processed_email_state_conflict")
    if event_type is not None:
        _add_activity_event(
            db,
            company_id=reservation.company_id,
            record_id=reservation.record_id,
            event_type=event_type,
        )
    db.commit()


def mark_done(
    db: Session,
    reservation: EmailReservation,
    *,
    category: str,
    priority: str,
    received_at: datetime,
) -> None:
    _set_status(
        db,
        reservation,
        status="done",
        last_error_code=None,
        category=category,
        priority=priority,
        received_at=received_at,
        event_type="email_processed",
    )


def mark_excluded(db: Session, reservation: EmailReservation) -> None:
    """Finalise un email hors fenêtre sans le qualifier comme analysé."""
    _set_status(
        db,
        reservation,
        status="excluded",
        last_error_code=None,
    )


def mark_skipped(
    db: Session,
    reservation: EmailReservation,
    *,
    category: str | None = None,
    priority: str | None = None,
    received_at: datetime | None = None,
) -> None:
    _set_status(
        db,
        reservation,
        status="skipped",
        last_error_code=None,
        category=category,
        priority=priority,
        received_at=received_at,
        event_type="email_skipped",
    )


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
            ProcessedEmail.attempts == reservation.attempt,
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


def list_pending_notifications(
    db: Session,
    *,
    company_id: int,
) -> tuple[tuple[int, str], ...]:
    """Liste uniquement les notifications réessayables de cette entreprise."""
    stale_before = datetime.now(UTC) - timedelta(
        seconds=max(0, settings.PIPELINE_PROCESSING_STALE_SECONDS)
    )
    rows = db.execute(
        select(ProcessedEmail.id, ProcessedEmail.callback_token).where(
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.status == "done",
            ProcessedEmail.gmail_draft_id.is_not(None),
            ProcessedEmail.callback_token.is_not(None),
            ProcessedEmail.notified_at.is_(None),
            ProcessedEmail.notification_blocked_at.is_(None),
            ProcessedEmail.notification_attempts < MAX_PROCESSING_ATTEMPTS,
            or_(
                ProcessedEmail.notification_claimed_at.is_(None),
                ProcessedEmail.notification_claimed_at < stale_before,
            ),
        )
    ).all()
    return tuple((record_id, callback_token) for record_id, callback_token in rows)


def claim_notification_attempt(
    db: Session,
    *,
    company_id: int,
    record_id: int,
) -> bool:
    """Consomme atomiquement une tentative Telegram, sans rouvrir le traitement."""
    stale_before = datetime.now(UTC) - timedelta(
        seconds=max(0, settings.PIPELINE_PROCESSING_STALE_SECONDS)
    )
    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.id == record_id,
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.status == "done",
            ProcessedEmail.gmail_draft_id.is_not(None),
            ProcessedEmail.callback_token.is_not(None),
            ProcessedEmail.notified_at.is_(None),
            ProcessedEmail.notification_blocked_at.is_(None),
            ProcessedEmail.notification_attempts < MAX_PROCESSING_ATTEMPTS,
            or_(
                ProcessedEmail.notification_claimed_at.is_(None),
                ProcessedEmail.notification_claimed_at < stale_before,
            ),
        )
        .values(
            notification_attempts=ProcessedEmail.notification_attempts + 1,
            notification_claimed_at=func.now(),
            updated_at=func.now(),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    db.commit()
    return True


def release_notification_claim(
    db: Session,
    *,
    company_id: int,
    record_id: int,
) -> None:
    """Rend réessayable une tentative Telegram qui a échoué proprement."""
    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.id == record_id,
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.notified_at.is_(None),
            ProcessedEmail.notification_blocked_at.is_(None),
            ProcessedEmail.notification_claimed_at.is_not(None),
        )
        .values(notification_claimed_at=None, updated_at=func.now())
    )
    if result.rowcount != 1:
        db.rollback()
        raise RuntimeError("processed_email_notification_conflict")
    db.commit()


def mark_notification_sent(
    db: Session,
    *,
    company_id: int,
    record_id: int,
) -> None:
    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.id == record_id,
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.notified_at.is_(None),
        )
        .values(
            notified_at=func.now(),
            notification_claimed_at=None,
            updated_at=func.now(),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise RuntimeError("processed_email_notification_conflict")
    _add_activity_event(
        db,
        company_id=company_id,
        record_id=record_id,
        event_type="notification_sent",
    )
    db.commit()


def mark_notification_blocked(
    db: Session,
    *,
    company_id: int,
    record_id: int,
) -> None:
    """Arrête définitivement les retries lorsque l'utilisateur bloque le bot."""
    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.id == record_id,
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.notified_at.is_(None),
            ProcessedEmail.notification_blocked_at.is_(None),
        )
        .values(
            notification_blocked_at=func.now(),
            notification_claimed_at=None,
            updated_at=func.now(),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        raise RuntimeError("processed_email_notification_conflict")
    db.commit()


def claim_draft_send(db: Session, *, company_id: int, record_id: int) -> bool:
    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.id == record_id,
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.draft_status == "pending",
        )
        .values(draft_status="sending", updated_at=func.now())
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    db.commit()
    return True


def claim_stale_draft_send_recovery(
    db: Session,
    *,
    company_id: int,
    record_id: int,
) -> bool:
    """Réserve la vérification Gmail d'un état sending devenu ancien."""
    stale_before = datetime.now(UTC) - timedelta(
        seconds=max(0, settings.PIPELINE_PROCESSING_STALE_SECONDS)
    )
    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.id == record_id,
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.draft_status == "sending",
            ProcessedEmail.updated_at < stale_before,
        )
        .values(updated_at=func.now())
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    db.commit()
    return True


def claim_draft_discard(db: Session, *, company_id: int, record_id: int) -> bool:
    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.id == record_id,
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.draft_status == "pending",
        )
        .values(
            draft_status="discarded",
            sheet_status_synced_at=None,
            updated_at=func.now(),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    _add_activity_event(
        db,
        company_id=company_id,
        record_id=record_id,
        event_type="draft_discarded",
    )
    db.commit()
    return True


def transition_draft_status(
    db: Session,
    *,
    company_id: int,
    record_id: int,
    expected_status: str,
    target_status: str,
) -> bool:
    if target_status not in {"pending", "sent", "missing"}:
        raise ValueError("processed_email_draft_status_invalid")
    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.id == record_id,
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.draft_status == expected_status,
        )
        .values(
            draft_status=target_status,
            sheet_status_synced_at=None,
            updated_at=func.now(),
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    if target_status == "sent":
        _add_activity_event(
            db,
            company_id=company_id,
            record_id=record_id,
            event_type="draft_sent",
        )
    db.commit()
    return True


def mark_draft_sheet_synced(
    db: Session,
    *,
    company_id: int,
    record_id: int,
    draft_status: str,
) -> bool:
    result = db.execute(
        update(ProcessedEmail)
        .where(
            ProcessedEmail.id == record_id,
            ProcessedEmail.company_id == company_id,
            ProcessedEmail.draft_status == draft_status,
        )
        .values(sheet_status_synced_at=func.now(), updated_at=func.now())
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    db.commit()
    return True
