"""Actions Telegram isolées par entreprise, parallèles au chemin historique."""

import re
from dataclasses import dataclass, field

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.modules.companies.models import Company
from app.modules.gmail.service import (
    GmailDraftNotFoundError,
    GmailTokenRevokedError,
    get_company_access_token,
    send_company_gmail_draft,
)
from app.modules.gmail_oauth.models import GmailConnection
from app.modules.gmail_oauth.security import decrypt_token
from app.modules.gmail_oauth.service import revoke_company_gmail_and_claim_notification
from app.modules.notifications.service import (
    CompanyTelegramError,
    answer_company_callback,
    send_company_gmail_revoked_notification,
    send_company_telegram_message,
)
from app.modules.processed_emails.models import ProcessedEmail
from app.modules.processed_emails.service import (
    claim_draft_discard,
    claim_draft_send,
    mark_draft_sheet_synced,
    transition_draft_status,
)
from app.modules.sheets.service import (
    get_company_draft_content,
    get_company_workbook,
    update_company_draft_status,
)
from app.modules.telegram.models import TelegramConnection

COMPANY_CALLBACK_PREFIX = "mt_"
_CALLBACK_PATTERN = re.compile(r"^mt_(view|send|discard)_([A-Za-z0-9_-]{16,43})$")
_GENERIC_CALLBACK_RESPONSE = "Action indisponible."


@dataclass(frozen=True)
class CompanyDraftContext:
    record_id: int
    company_id: int
    user_id: int
    sheet_id: str = field(repr=False)
    encrypted_refresh_token: str = field(repr=False)
    gmail_draft_id: str = field(repr=False)
    callback_token: str = field(repr=False)
    gmail_status: str
    draft_status: str
    sheet_status_synced: bool

    @property
    def sheet_email_id(self) -> str:
        return f"EM-{self.record_id}"


def build_company_callback_data(action: str, callback_token: str) -> str:
    if action not in {"view", "send", "discard"}:
        raise ValueError("company_callback_action_invalid")
    data = f"{COMPANY_CALLBACK_PREFIX}{action}_{callback_token}"
    if len(data.encode("utf-8")) > 64 or _CALLBACK_PATTERN.fullmatch(data) is None:
        raise ValueError("company_callback_data_invalid")
    return data


def parse_company_callback_data(data: str) -> tuple[str, str] | None:
    match = _CALLBACK_PATTERN.fullmatch(data)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _resolve_context(
    db: Session,
    *,
    telegram_chat_id: int,
    telegram_user_id: int,
    callback_token: str,
) -> CompanyDraftContext | None:
    """Résout chat → utilisateur → entreprise avant de regarder le brouillon."""
    row = db.execute(
        select(ProcessedEmail, Company, GmailConnection)
        .join(Company, Company.id == ProcessedEmail.company_id)
        .join(
            TelegramConnection,
            and_(
                TelegramConnection.user_id == Company.user_id,
                TelegramConnection.telegram_chat_id == telegram_chat_id,
                TelegramConnection.telegram_user_id == telegram_user_id,
            ),
        )
        .join(GmailConnection, GmailConnection.user_id == Company.user_id)
        .where(
            ProcessedEmail.company_id == Company.id,
            ProcessedEmail.callback_token == callback_token,
            ProcessedEmail.gmail_draft_id.is_not(None),
        )
    ).one_or_none()
    if row is None:
        return None
    record, company, gmail_connection = row
    if record.draft_status is None:
        return None
    return CompanyDraftContext(
        record_id=record.id,
        company_id=company.id,
        user_id=company.user_id,
        sheet_id=company.sheet_id,
        encrypted_refresh_token=gmail_connection.encrypted_refresh_token,
        gmail_draft_id=record.gmail_draft_id,
        callback_token=record.callback_token,
        gmail_status=gmail_connection.status,
        draft_status=record.draft_status,
        sheet_status_synced=record.sheet_status_synced_at is not None,
    )


async def _safe_answer(callback_id: str, text: str) -> None:
    try:
        await answer_company_callback(callback_id, text)
    except CompanyTelegramError:
        pass


async def _safe_send(
    chat_id: int, text: str, *, reply_markup: dict | None = None
) -> None:
    try:
        await send_company_telegram_message(
            chat_id,
            text,
            reply_markup=reply_markup,
        )
    except CompanyTelegramError:
        pass


def _current_draft_status(
    db: Session,
    *,
    company_id: int,
    record_id: int,
) -> tuple[str | None, bool]:
    row = db.execute(
        select(
            ProcessedEmail.draft_status,
            ProcessedEmail.sheet_status_synced_at,
        ).where(
            ProcessedEmail.id == record_id,
            ProcessedEmail.company_id == company_id,
        )
    ).one_or_none()
    if row is None:
        return None, False
    return row[0], row[1] is not None


def _sync_sheet_status(
    db: Session,
    context: CompanyDraftContext,
    *,
    draft_status: str,
) -> bool:
    sheet_status = {"sent": "Envoyé", "discarded": "Ignoré"}.get(draft_status)
    if sheet_status is None:
        return False
    try:
        workbook = get_company_workbook(context.sheet_id)
        update_company_draft_status(
            workbook,
            email_id=context.sheet_email_id,
            draft_id=context.gmail_draft_id,
            statut=sheet_status,
        )
    except Exception:
        return False
    return mark_draft_sheet_synced(
        db,
        company_id=context.company_id,
        record_id=context.record_id,
        draft_status=draft_status,
    )


async def _answer_existing_state(
    db: Session,
    context: CompanyDraftContext,
    callback_id: str,
) -> None:
    draft_status, synced = _current_draft_status(
        db,
        company_id=context.company_id,
        record_id=context.record_id,
    )
    if draft_status == "sent":
        if not synced:
            _sync_sheet_status(db, context, draft_status="sent")
        await _safe_answer(callback_id, "Ce brouillon a déjà été envoyé.")
    elif draft_status == "discarded":
        if not synced:
            _sync_sheet_status(db, context, draft_status="discarded")
        await _safe_answer(callback_id, "Ce brouillon a déjà été ignoré.")
    elif draft_status == "missing":
        await _safe_answer(callback_id, "Ce brouillon n’existe plus dans Gmail.")
    elif draft_status == "sending":
        await _safe_answer(callback_id, "L’envoi est déjà en cours.")
    else:
        await _safe_answer(callback_id, _GENERIC_CALLBACK_RESPONSE)


def _draft_view_text(content: str) -> str:
    prefix = "Brouillon proposé\n\n"
    available = 4096 - len(prefix)
    if len(content) <= available:
        return prefix + content
    return prefix + content[: max(0, available - 1)].rstrip() + "…"


async def _handle_view(
    db: Session,
    context: CompanyDraftContext,
    *,
    chat_id: int,
    callback_id: str,
) -> None:
    if context.draft_status != "pending":
        await _answer_existing_state(db, context, callback_id)
        return
    try:
        workbook = get_company_workbook(context.sheet_id)
        content = get_company_draft_content(
            workbook,
            email_id=context.sheet_email_id,
            draft_id=context.gmail_draft_id,
        )
    except Exception:
        content = None
    if content is None:
        await _safe_answer(callback_id, "Brouillon introuvable.")
        return

    send_data = build_company_callback_data("send", context.callback_token)
    discard_data = build_company_callback_data("discard", context.callback_token)
    await _safe_answer(callback_id, "Brouillon chargé.")
    await _safe_send(
        chat_id,
        _draft_view_text(content),
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "Envoyer", "callback_data": send_data},
                    {"text": "Ignorer", "callback_data": discard_data},
                ]
            ]
        },
    )


async def _notify_revocation_once(
    db: Session,
    context: CompanyDraftContext,
    *,
    chat_id: int,
) -> None:
    if not revoke_company_gmail_and_claim_notification(
        db,
        company_id=context.company_id,
    ):
        return
    try:
        await send_company_gmail_revoked_notification(chat_id)
    except CompanyTelegramError:
        pass


async def _handle_send(
    db: Session,
    context: CompanyDraftContext,
    *,
    chat_id: int,
    callback_id: str,
) -> None:
    if context.gmail_status != "connected":
        await _safe_answer(callback_id, "Reconnectez Gmail depuis vos intégrations.")
        return
    if context.draft_status != "pending" or not claim_draft_send(
        db,
        company_id=context.company_id,
        record_id=context.record_id,
    ):
        await _answer_existing_state(db, context, callback_id)
        return

    await _safe_answer(callback_id, "Envoi en cours…")
    try:
        refresh_token = decrypt_token(context.encrypted_refresh_token)
        access_token = await get_company_access_token(refresh_token)
        await send_company_gmail_draft(access_token, context.gmail_draft_id)
    except GmailTokenRevokedError:
        transition_draft_status(
            db,
            company_id=context.company_id,
            record_id=context.record_id,
            expected_status="sending",
            target_status="pending",
        )
        await _notify_revocation_once(db, context, chat_id=chat_id)
        return
    except GmailDraftNotFoundError:
        transition_draft_status(
            db,
            company_id=context.company_id,
            record_id=context.record_id,
            expected_status="sending",
            target_status="missing",
        )
        await _safe_send(chat_id, "Ce brouillon n’existe plus dans Gmail.")
        return
    except Exception:
        transition_draft_status(
            db,
            company_id=context.company_id,
            record_id=context.record_id,
            expected_status="sending",
            target_status="pending",
        )
        await _safe_send(chat_id, "L’envoi a échoué. Le brouillon reste disponible.")
        return

    transition_draft_status(
        db,
        company_id=context.company_id,
        record_id=context.record_id,
        expected_status="sending",
        target_status="sent",
    )
    _sync_sheet_status(db, context, draft_status="sent")
    await _safe_send(chat_id, "Brouillon envoyé.")


async def _handle_discard(
    db: Session,
    context: CompanyDraftContext,
    *,
    callback_id: str,
) -> None:
    if context.draft_status != "pending" or not claim_draft_discard(
        db,
        company_id=context.company_id,
        record_id=context.record_id,
    ):
        await _answer_existing_state(db, context, callback_id)
        return
    _sync_sheet_status(db, context, draft_status="discarded")
    await _safe_answer(callback_id, "Brouillon ignoré.")


async def handle_company_callback_query(query: dict, db: Session) -> None:
    """Point d'entrée sûr des callbacks `mt_`; aucune donnée n'est renvoyée."""
    callback_id = query.get("id")
    if not isinstance(callback_id, str) or not callback_id:
        return
    parsed = parse_company_callback_data(str(query.get("data", "")))
    chat_id = query.get("message", {}).get("chat", {}).get("id")
    telegram_user_id = query.get("from", {}).get("id")
    if (
        parsed is None
        or not isinstance(chat_id, int)
        or not isinstance(telegram_user_id, int)
    ):
        await _safe_answer(callback_id, _GENERIC_CALLBACK_RESPONSE)
        return

    action, callback_token = parsed
    context = _resolve_context(
        db,
        telegram_chat_id=chat_id,
        telegram_user_id=telegram_user_id,
        callback_token=callback_token,
    )
    if context is None:
        await _safe_answer(callback_id, _GENERIC_CALLBACK_RESPONSE)
        return

    if action == "view":
        await _handle_view(
            db,
            context,
            chat_id=chat_id,
            callback_id=callback_id,
        )
    elif action == "send":
        await _handle_send(
            db,
            context,
            chat_id=chat_id,
            callback_id=callback_id,
        )
    else:
        await _handle_discard(db, context, callback_id=callback_id)
