"""Flux Gmail → filtre → Gemini → Sheet → brouillon, isolé par entreprise."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Callable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import SessionLocal
from app.modules.actions.company_service import build_company_callback_data
from app.modules.ai.service import analyze_email
from app.modules.companies.models import Company
from app.modules.drafts.service import create_company_gmail_draft
from app.modules.filtering.service import should_process
from app.modules.gmail.service import (
    CompanyGmailError,
    GmailQuotaError,
    GmailTokenRevokedError,
    get_company_access_token,
    get_company_email,
    list_company_message_page,
)
from app.modules.gmail_oauth.models import GmailConnection
from app.modules.gmail_oauth.security import decrypt_token
from app.modules.gmail_oauth.service import revoke_company_gmail_and_claim_notification
from app.modules.notifications.service import (
    CompanyTelegramBlockedError,
    CompanyTelegramError,
    send_company_email_notification,
    send_company_gmail_revoked_notification,
)
from app.modules.pipeline_selection.service import (
    CompanyRunPlan,
    PreparedSimulationRun,
    received_after_activation,
)
from app.modules.processed_emails.service import (
    EmailReservation,
    claim_notification_attempt,
    list_pending_notifications,
    mark_done,
    mark_failed,
    mark_notification_blocked,
    mark_notification_sent,
    mark_skipped,
    record_gmail_draft_id,
    release_after_quota,
    release_notification_claim,
    reserve_email,
)
from app.modules.sheets.service import (
    get_company_draft_id,
    get_company_notification_data,
    get_company_workbook,
    mark_company_email_draft_ready,
    upsert_company_draft,
    upsert_company_email,
)
from app.modules.telegram.models import TelegramConnection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompanyExecutionContext:
    """Identifiants internes et secrets strictement réservés à une entreprise."""

    company_id: int
    sheet_id: str = field(repr=False)
    encrypted_refresh_token: str = field(repr=False)
    telegram_chat_id: int = field(repr=False)
    pipeline_activated_at: datetime
    email_limit: int


@dataclass
class CompanyRunCounters:
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    quota_stopped: bool = False
    token_revoked: bool = False
    company_failed: bool = False


@dataclass
class PipelineRunSummary:
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    attempted_company_ids: list[int] = field(default_factory=list)
    revoked_company_ids: list[int] = field(default_factory=list)
    quota_stopped_company_ids: list[int] = field(default_factory=list)
    failed_company_ids: list[int] = field(default_factory=list)

    def add_company(self, company_id: int, counters: CompanyRunCounters) -> None:
        self.processed += counters.processed
        self.skipped += counters.skipped
        self.failed += counters.failed
        self.attempted_company_ids.append(company_id)
        if counters.company_failed:
            self.failed_company_ids.append(company_id)
        if counters.token_revoked:
            self.revoked_company_ids.append(company_id)
        if counters.quota_stopped:
            self.quota_stopped_company_ids.append(company_id)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _activate_and_load_context(
    session_factory: Callable[[], Session],
    plan: CompanyRunPlan,
) -> CompanyExecutionContext | None:
    """Active avant tout accès externe et recharge les trois intégrations."""
    with session_factory() as db:
        row = db.execute(
            select(Company, GmailConnection, TelegramConnection)
            .join(GmailConnection, GmailConnection.user_id == Company.user_id)
            .join(TelegramConnection, TelegramConnection.user_id == Company.user_id)
            .where(
                Company.id == plan.company_id,
                GmailConnection.status == "connected",
                func.length(func.trim(GmailConnection.encrypted_refresh_token)) > 0,
                func.length(func.trim(Company.sheet_id)) > 0,
            )
        ).one_or_none()
        if row is None:
            db.rollback()
            return None
        company, gmail_connection, telegram_connection = row
        company_id = company.id
        sheet_id = company.sheet_id
        encrypted_refresh_token = gmail_connection.encrypted_refresh_token
        activated_at = company.pipeline_activated_at
        if activated_at is None:
            activated_at = _utcnow()
            company.pipeline_activated_at = activated_at
        # Persiste l'activation et ferme la transaction avant tout accès externe.
        db.commit()
        return CompanyExecutionContext(
            company_id=company_id,
            sheet_id=sheet_id,
            encrypted_refresh_token=encrypted_refresh_token,
            telegram_chat_id=telegram_connection.telegram_chat_id,
            pipeline_activated_at=_as_utc(activated_at),
            email_limit=plan.email_limit,
        )


async def _revoke_company_gmail(
    session_factory: Callable[[], Session],
    context: CompanyExecutionContext,
) -> None:
    with session_factory() as db:
        should_notify = revoke_company_gmail_and_claim_notification(
            db,
            company_id=context.company_id,
        )
    if not should_notify:
        return
    try:
        await send_company_gmail_revoked_notification(context.telegram_chat_id)
    except CompanyTelegramError:
        # Le marqueur rend cet avis au plus une fois, y compris si le bot est bloqué.
        pass


def _mark_company_pass_finished(
    session_factory: Callable[[], Session],
    company_id: int,
) -> None:
    with session_factory() as db:
        db.execute(
            update(Company)
            .where(Company.id == company_id)
            .values(pipeline_last_processed_at=_utcnow())
        )
        db.commit()


def _with_record_session(
    session_factory: Callable[[], Session],
    operation: Callable[[Session], None],
) -> None:
    with session_factory() as db:
        operation(db)


def _reserve(
    session_factory: Callable[[], Session],
    company_id: int,
    gmail_message_id: str,
) -> EmailReservation | None:
    with session_factory() as db:
        return reserve_email(
            db,
            company_id=company_id,
            gmail_message_id=gmail_message_id,
        )


def _record_draft(
    session_factory: Callable[[], Session],
    reservation: EmailReservation,
    draft_id: str,
) -> EmailReservation:
    with session_factory() as db:
        return record_gmail_draft_id(
            db,
            reservation,
            gmail_draft_id=draft_id,
        )


async def _attempt_company_notification(
    *,
    session_factory: Callable[[], Session],
    context: CompanyExecutionContext,
    record_id: int,
    callback_token: str,
    notification_data: dict,
) -> bool:
    with session_factory() as db:
        claimed = claim_notification_attempt(
            db,
            company_id=context.company_id,
            record_id=record_id,
        )
    if not claimed:
        return False

    try:
        await send_company_email_notification(
            context.telegram_chat_id,
            expediteur=str(notification_data["expediteur"]),
            objet=str(notification_data["objet"]),
            categorie=str(notification_data["categorie"]),
            priorite=str(notification_data["priorite"]),
            resume=str(notification_data["resume"]),
            view_callback_data=build_company_callback_data(
                "view",
                callback_token,
            ),
        )
    except CompanyTelegramBlockedError:
        _with_record_session(
            session_factory,
            lambda db: mark_notification_blocked(
                db,
                company_id=context.company_id,
                record_id=record_id,
            ),
        )
        return False
    except Exception:
        # La tentative reste comptée, mais le traitement Gmail/Gemini est terminé.
        _with_record_session(
            session_factory,
            lambda db: release_notification_claim(
                db,
                company_id=context.company_id,
                record_id=record_id,
            ),
        )
        return False

    _with_record_session(
        session_factory,
        lambda db: mark_notification_sent(
            db,
            company_id=context.company_id,
            record_id=record_id,
        ),
    )
    return True


async def _retry_pending_notifications(
    *,
    session_factory: Callable[[], Session],
    context: CompanyExecutionContext,
    workbook,
):
    with session_factory() as db:
        pending = list_pending_notifications(db, company_id=context.company_id)
    if not pending:
        return workbook
    try:
        workbook = workbook or get_company_workbook(context.sheet_id)
    except Exception:
        return workbook

    for record_id, callback_token in pending:
        try:
            notification_data = get_company_notification_data(
                workbook,
                email_id=f"EM-{record_id}",
            )
        except Exception:
            continue
        if notification_data is None:
            continue
        try:
            await _attempt_company_notification(
                session_factory=session_factory,
                context=context,
                record_id=record_id,
                callback_token=callback_token,
                notification_data=notification_data,
            )
        except Exception:
            continue
    return workbook


def _status_code_from_exception(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    code = getattr(response, "status", None)
    return code if isinstance(code, int) else None


def _is_quota_error(exc: Exception) -> bool:
    if isinstance(exc, GmailQuotaError):
        return True
    if _status_code_from_exception(exc) == 429:
        return True
    # Le service Gemini encode seulement le statut HTTP dans RuntimeError.
    return "HTTP 429" in str(exc)


def _safe_error_code(stage: str, exc: Exception) -> str:
    if isinstance(exc, CompanyGmailError):
        return exc.code
    status_code = _status_code_from_exception(exc)
    if status_code is not None:
        return f"{stage}_http_{status_code}"
    return f"{stage}_error"


def _reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def _recipient(sender: str) -> str:
    address = parseaddr(sender)[1]
    if not address:
        raise ValueError("sender_address_missing")
    return address


async def _process_reserved_email(
    *,
    session_factory: Callable[[], Session],
    context: CompanyExecutionContext,
    access_token: str,
    reservation: EmailReservation,
    email: dict,
    workbook,
) -> tuple[str, object | None]:
    """Traite une réservation; retourne done/skipped et le workbook réutilisable."""
    stage = "filter"
    try:
        decision = should_process(
            email["expediteur"],
            email["corps"] or email["snippet"],
            email.get("list_unsubscribe", ""),
        )
        if decision["decision"] == "ignorer":
            stage = "sheets"
            workbook = workbook or get_company_workbook(context.sheet_id)
            upsert_company_email(
                workbook,
                email_id=reservation.sheet_email_id,
                expediteur=email["expediteur"],
                objet=email["objet"],
                resume=decision["raison"],
                categorie="",
                priorite="",
                statut="Ignoré",
            )
            _with_record_session(
                session_factory,
                lambda db: mark_skipped(db, reservation),
            )
            return "skipped", workbook

        stage = "gemini"
        body_limit = max(0, settings.GEMINI_MAX_EMAIL_BODY_CHARS)
        analysis = await analyze_email(
            email["expediteur"],
            email["objet"],
            email["corps"][:body_limit],
        )
        required_keys = {"urgence", "categorie", "resume", "brouillon"}
        if not isinstance(analysis, dict) or not required_keys.issubset(analysis):
            raise ValueError("gemini_response_invalid")

        workbook = workbook or get_company_workbook(context.sheet_id)
        if analysis["categorie"] in {"newsletter", "spam"}:
            stage = "sheets"
            upsert_company_email(
                workbook,
                email_id=reservation.sheet_email_id,
                expediteur=email["expediteur"],
                objet=email["objet"],
                resume=analysis["resume"],
                categorie=analysis["categorie"],
                priorite=analysis["urgence"],
                statut="Ignoré",
            )
            _with_record_session(
                session_factory,
                lambda db: mark_skipped(db, reservation),
            )
            return "skipped", workbook

        stage = "sheets"
        upsert_company_email(
            workbook,
            email_id=reservation.sheet_email_id,
            expediteur=email["expediteur"],
            objet=email["objet"],
            resume=analysis["resume"],
            categorie=analysis["categorie"],
            priorite=analysis["urgence"],
            statut="Analysé",
        )
        # Neon est la source d'idempotence prioritaire. Le Sheet permet aussi de
        # récupérer les brouillons créés avant l'ajout de ce champ au registre.
        draft_id = reservation.gmail_draft_id
        if draft_id is None:
            draft_id = get_company_draft_id(
                workbook,
                email_id=reservation.sheet_email_id,
            )
            if draft_id is not None:
                reservation = _record_draft(
                    session_factory,
                    reservation,
                    draft_id,
                )
        if draft_id is None:
            stage = "gmail_draft"
            thread_id = email.get("thread_id")
            if not isinstance(thread_id, str) or not thread_id:
                raise ValueError("gmail_thread_missing")
            draft_id = await create_company_gmail_draft(
                access_token,
                destinataire=_recipient(email["expediteur"]),
                objet=_reply_subject(email["objet"]),
                corps=analysis["brouillon"],
                thread_id=thread_id,
                in_reply_to=email.get("message_id_header", ""),
            )
            # Cette transaction est validée avant toute écriture Sheet : un
            # nouvel essai ne peut donc pas créer un second brouillon Gmail.
            reservation = _record_draft(
                session_factory,
                reservation,
                draft_id,
            )
        stage = "sheets"
        upsert_company_draft(
            workbook,
            draft_id=draft_id,
            email_id=reservation.sheet_email_id,
            contenu=analysis["brouillon"],
            statut="En attente",
        )
        mark_company_email_draft_ready(
            workbook,
            email_id=reservation.sheet_email_id,
            draft_id=draft_id,
        )
        _with_record_session(
            session_factory,
            lambda db: mark_done(db, reservation),
        )
        if reservation.callback_token is not None:
            try:
                await _attempt_company_notification(
                    session_factory=session_factory,
                    context=context,
                    record_id=reservation.record_id,
                    callback_token=reservation.callback_token,
                    notification_data={
                        "expediteur": email["expediteur"],
                        "objet": email["objet"],
                        "categorie": analysis["categorie"],
                        "priorite": analysis["urgence"],
                        "resume": analysis["resume"],
                    },
                )
            except Exception:
                # La notification reste indépendante du traitement désormais done.
                pass
        return "done", workbook
    except Exception as exc:
        if _is_quota_error(exc):
            _with_record_session(
                session_factory,
                lambda db: release_after_quota(
                    db,
                    reservation,
                    error_code=f"{stage}_quota_429",
                ),
            )
            raise GmailQuotaError(f"{stage}_quota_429", status_code=429) from None
        error_code = _safe_error_code(stage, exc)
        _with_record_session(
            session_factory,
            lambda db: mark_failed(
                db,
                reservation,
                error_code=error_code,
            ),
        )
        return "failed", workbook


async def _run_company(
    *,
    plan: CompanyRunPlan,
    budget,
    session_factory: Callable[[], Session],
) -> CompanyRunCounters:
    counters = CompanyRunCounters()
    context = _activate_and_load_context(session_factory, plan)
    if context is None:
        return counters

    workbook = None
    workbook = await _retry_pending_notifications(
        session_factory=session_factory,
        context=context,
        workbook=workbook,
    )
    try:
        refresh_token = decrypt_token(context.encrypted_refresh_token)
        access_token = await get_company_access_token(refresh_token)
    except GmailTokenRevokedError:
        await _revoke_company_gmail(session_factory, context)
        counters.token_revoked = True
        _mark_company_pass_finished(session_factory, context.company_id)
        return counters
    except GmailQuotaError:
        counters.quota_stopped = True
        _mark_company_pass_finished(session_factory, context.company_id)
        return counters
    except Exception:
        counters.company_failed = True
        _mark_company_pass_finished(session_factory, context.company_id)
        return counters

    after_epoch_seconds = int(context.pipeline_activated_at.timestamp())
    page_token: str | None = None
    seen_page_tokens: set[str] = set()
    reserved_count = 0

    try:
        while reserved_count < context.email_limit and budget.has_time_remaining():
            message_ids, next_page_token = await list_company_message_page(
                access_token,
                after_epoch_seconds=after_epoch_seconds,
                page_token=page_token,
                page_size=max(20, context.email_limit * 2),
            )
            for gmail_message_id in message_ids:
                if (
                    reserved_count >= context.email_limit
                    or not budget.has_time_remaining()
                ):
                    break
                reservation = _reserve(
                    session_factory,
                    context.company_id,
                    gmail_message_id,
                )
                if reservation is None:
                    continue
                reserved_count += 1
                try:
                    email = await get_company_email(access_token, gmail_message_id)
                except Exception as exc:
                    if _is_quota_error(exc):
                        _with_record_session(
                            session_factory,
                            lambda db: release_after_quota(db, reservation),
                        )
                        counters.quota_stopped = True
                        return counters
                    error_code = _safe_error_code("gmail_detail", exc)
                    _with_record_session(
                        session_factory,
                        lambda db: mark_failed(
                            db,
                            reservation,
                            error_code=error_code,
                        ),
                    )
                    counters.failed += 1
                    continue

                if not received_after_activation(
                    email["received_at"],
                    context.pipeline_activated_at,
                ):
                    _with_record_session(
                        session_factory,
                        lambda db: mark_skipped(db, reservation),
                    )
                    counters.skipped += 1
                    continue
                try:
                    outcome, workbook = await _process_reserved_email(
                        session_factory=session_factory,
                        context=context,
                        access_token=access_token,
                        reservation=reservation,
                        email=email,
                        workbook=workbook,
                    )
                except GmailQuotaError:
                    # Le traitement a déjà annulé le coût de cette tentative.
                    counters.quota_stopped = True
                    return counters
                if outcome == "done":
                    counters.processed += 1
                elif outcome == "skipped":
                    counters.skipped += 1
                else:
                    counters.failed += 1

            if not next_page_token or next_page_token in seen_page_tokens:
                break
            seen_page_tokens.add(next_page_token)
            page_token = next_page_token
    except GmailQuotaError:
        counters.quota_stopped = True
    except Exception:
        counters.company_failed = True
    finally:
        _mark_company_pass_finished(session_factory, context.company_id)
    return counters


async def execute_multi_tenant_run(
    prepared: PreparedSimulationRun,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
) -> PipelineRunSummary:
    """Traite l'intersection sélectionnée/allowlist, entreprise par entreprise."""
    summary = PipelineRunSummary()
    processing_ids = set(prepared.result.processing_company_ids)
    for plan in prepared.plans:
        if plan.company_id not in processing_ids:
            continue
        if not prepared.budget.has_time_remaining():
            break
        try:
            counters = await _run_company(
                plan=plan,
                budget=prepared.budget,
                session_factory=session_factory,
            )
            summary.add_company(plan.company_id, counters)
        except Exception:
            summary.failed_company_ids.append(plan.company_id)
    return summary


async def complete_multi_tenant_run(prepared: PreparedSimulationRun) -> None:
    """Exécute après le 202 puis libère toujours le verrou transactionnel."""
    try:
        summary = await execute_multi_tenant_run(prepared)
        logger.info(
            "Pipeline multi-tenant terminé: attempted_company_ids=%s "
            "revoked_company_ids=%s quota_stopped_company_ids=%s "
            "failed_company_ids=%s processed=%d skipped=%d failed=%d",
            summary.attempted_company_ids,
            summary.revoked_company_ids,
            summary.quota_stopped_company_ids,
            summary.failed_company_ids,
            summary.processed,
            summary.skipped,
            summary.failed,
        )
    except Exception as exc:
        logger.error(
            "Pipeline multi-tenant interrompu: error_type=%s",
            type(exc).__name__,
        )
    finally:
        if prepared.db is not None:
            try:
                prepared.db.rollback()
            finally:
                prepared.db.close()
