"""Liaison Telegram stockée dans Neon et traitement du webhook du bot."""

import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.sheets_client import get_central_workbook
from app.modules.auth.models import User
from app.modules.telegram.models import TelegramConnection, TelegramLinkCode

TELEGRAM_API_URL = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"
LINK_CODE_TTL = timedelta(minutes=15)
LINK_CODE_GENERATION_ATTEMPTS = 10


class TelegramAlreadyConnectedError(ValueError):
    """L'utilisateur possède déjà la connexion unique autorisée."""


class LinkCodeGenerationError(RuntimeError):
    """Aucun code aléatoire unique n'a pu être créé."""


class LinkCodeClaimError(RuntimeError):
    """La consommation atomique d'un code a échoué."""


class LinkCodeUseResult(str, Enum):
    SUCCESS = "success"
    INVALID = "invalid"
    EXPIRED = "expired"
    USED = "used"
    USER_ALREADY_CONNECTED = "user_already_connected"
    CHAT_ALREADY_CONNECTED = "chat_already_connected"


async def send_telegram_message(chat_id: int, text: str) -> None:
    """Envoie un message Telegram sans journaliser son destinataire."""
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(url, json=payload)


def _generate_link_code() -> str:
    """Génère un code cryptographiquement aléatoire au format SR-XXXXX."""
    digits = "".join(secrets.choice("0123456789") for _ in range(5))
    return f"SR-{digits}"


def _as_utc(value: datetime) -> datetime:
    """Normalise aussi les datetimes naïves produites par SQLite en test."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_connection_by_user_id(db: Session, user_id: int) -> TelegramConnection | None:
    return db.execute(
        select(TelegramConnection).where(TelegramConnection.user_id == user_id)
    ).scalar_one_or_none()


def create_link_code(
    db: Session,
    user_id: int,
    *,
    now: datetime | None = None,
) -> TelegramLinkCode:
    """Invalide l'ancien code pending et crée un code valable 15 minutes."""
    current_time = now or datetime.now(timezone.utc)
    for _ in range(LINK_CODE_GENERATION_ATTEMPTS):
        try:
            # Le verrou utilisateur sérialise deux générations concurrentes.
            user_exists = db.execute(
                select(User.id).where(User.id == user_id).with_for_update()
            ).scalar_one_or_none()
            if user_exists is None:
                db.rollback()
                raise ValueError("Utilisateur introuvable")
            if get_connection_by_user_id(db, user_id) is not None:
                db.rollback()
                raise TelegramAlreadyConnectedError(
                    "Ce compte SmartReply est déjà connecté à Telegram."
                )

            db.execute(
                update(TelegramLinkCode)
                .where(
                    TelegramLinkCode.user_id == user_id,
                    TelegramLinkCode.status == "pending",
                )
                .values(status="expired")
            )
            link_code = TelegramLinkCode(
                user_id=user_id,
                code=_generate_link_code(),
                status="pending",
                expires_at=current_time + LINK_CODE_TTL,
            )
            db.add(link_code)
            db.commit()
            db.refresh(link_code)
            return link_code
        except IntegrityError:
            # Collision de code extrêmement rare ou génération concurrente :
            # la transaction entière est rejouée avec un nouveau code.
            db.rollback()
        except Exception:
            db.rollback()
            raise

    raise LinkCodeGenerationError(
        "Impossible de générer un code Telegram unique. Réessayez."
    )


def get_connection_status(db: Session, user_id: int) -> tuple[bool, datetime | None]:
    connection = get_connection_by_user_id(db, user_id)
    if connection is None:
        return False, None
    return True, _as_utc(connection.connected_at)


def consume_link_code(
    db: Session,
    code: str,
    telegram_chat_id: int,
    telegram_user_id: int,
    *,
    now: datetime | None = None,
) -> LinkCodeUseResult:
    """Consomme un code sous verrou et crée une liaison 1-1 atomique."""
    normalized_code = code.strip().upper()
    current_time = now or datetime.now(timezone.utc)

    try:
        candidate_user_id = db.execute(
            select(TelegramLinkCode.user_id).where(
                TelegramLinkCode.code == normalized_code
            )
        ).scalar_one_or_none()
        if candidate_user_id is None:
            db.rollback()
            return LinkCodeUseResult.INVALID

        # Même ordre de verrouillage que la génération : utilisateur, puis code.
        db.execute(
            select(User.id).where(User.id == candidate_user_id).with_for_update()
        ).scalar_one()
        link_code = db.execute(
            select(TelegramLinkCode)
            .where(TelegramLinkCode.code == normalized_code)
            .with_for_update()
        ).scalar_one()

        if link_code.status == "used":
            db.rollback()
            return LinkCodeUseResult.USED
        if (
            link_code.status == "expired"
            or _as_utc(link_code.expires_at) <= current_time
        ):
            if link_code.status == "pending":
                link_code.status = "expired"
                db.commit()
            else:
                db.rollback()
            return LinkCodeUseResult.EXPIRED
        if link_code.status != "pending":
            db.rollback()
            return LinkCodeUseResult.INVALID

        user_connection = get_connection_by_user_id(db, link_code.user_id)
        if user_connection is not None:
            link_code.status = "expired"
            db.commit()
            return LinkCodeUseResult.USER_ALREADY_CONNECTED

        chat_connection = db.execute(
            select(TelegramConnection).where(
                TelegramConnection.telegram_chat_id == telegram_chat_id
            )
        ).scalar_one_or_none()
        if chat_connection is not None:
            db.rollback()
            return LinkCodeUseResult.CHAT_ALREADY_CONNECTED

        db.add(
            TelegramConnection(
                user_id=link_code.user_id,
                telegram_chat_id=telegram_chat_id,
                telegram_user_id=telegram_user_id,
                connected_at=current_time,
            )
        )
        link_code.status = "used"
        link_code.used_at = current_time
        db.commit()
        return LinkCodeUseResult.SUCCESS
    except IntegrityError as exc:
        # Les contraintes UNIQUE restent l'ultime protection lorsque deux
        # transactions portant sur des codes différents se croisent.
        db.rollback()
        chat_connection = db.execute(
            select(TelegramConnection).where(
                TelegramConnection.telegram_chat_id == telegram_chat_id
            )
        ).scalar_one_or_none()
        if chat_connection is not None:
            db.rollback()
            return LinkCodeUseResult.CHAT_ALREADY_CONNECTED

        user_connection = db.execute(
            select(TelegramConnection)
            .join(
                TelegramLinkCode,
                TelegramLinkCode.user_id == TelegramConnection.user_id,
            )
            .where(TelegramLinkCode.code == normalized_code)
        ).scalar_one_or_none()
        if user_connection is not None:
            pending_code = db.execute(
                select(TelegramLinkCode).where(TelegramLinkCode.code == normalized_code)
            ).scalar_one_or_none()
            if pending_code is not None and pending_code.status == "pending":
                pending_code.status = "expired"
                db.commit()
            else:
                db.rollback()
            return LinkCodeUseResult.USER_ALREADY_CONNECTED

        db.rollback()
        raise LinkCodeClaimError(
            "Impossible de terminer la connexion Telegram."
        ) from exc
    except Exception:
        db.rollback()
        raise


async def process_telegram_update(update: dict, db: Session) -> None:
    if "callback_query" in update:
        from app.modules.actions.service import handle_callback_query

        await handle_callback_query(update["callback_query"])
        return

    message = update.get("message")
    if not isinstance(message, dict):
        return

    chat_id = message.get("chat", {}).get("id")
    telegram_user_id = message.get("from", {}).get("id")
    if not isinstance(chat_id, int) or not isinstance(telegram_user_id, int):
        return
    text = str(message.get("text", "")).strip()

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            await handle_connection_code(chat_id, telegram_user_id, parts[1], db)
        else:
            await send_telegram_message(
                chat_id,
                "👋 *Bienvenue sur SmartReply Agent !*\n\n"
                "Pour connecter votre compte Telegram, envoyez votre code de "
                "connexion (ex: `SR-82941`).\n\n"
                "💡 *Obtenez votre code depuis SmartReply.*",
            )
    elif text == "/status":
        await check_user_status(chat_id, db)
    elif text == "/help":
        await send_telegram_message(
            chat_id,
            "❓ *Aide - SmartReply Agent*\n\n"
            "• `/start` — Démarrer le bot\n"
            "• `/status` — Vérifier votre état de connexion\n"
            "• `/help` — Afficher l'aide",
        )
    elif text.upper().startswith("SR-"):
        await handle_connection_code(chat_id, telegram_user_id, text, db)
    else:
        await send_telegram_message(
            chat_id,
            "🤖 Commande non reconnue. Envoyez un code valide "
            "(ex: `SR-82941`) ou `/help`.",
        )


async def handle_connection_code(
    chat_id: int,
    telegram_user_id: int,
    code: str,
    db: Session,
) -> None:
    """Valide atomiquement un code Neon et répond sans le journaliser."""
    try:
        result = consume_link_code(
            db,
            code,
            telegram_chat_id=chat_id,
            telegram_user_id=telegram_user_id,
        )
    except Exception:
        await send_telegram_message(
            chat_id,
            "⚠️ Une erreur est survenue lors de la vérification du code.",
        )
        return

    messages = {
        LinkCodeUseResult.SUCCESS: (
            "🎉 *Connexion réussie !*\n\nVotre compte Telegram est maintenant "
            "connecté à SmartReply Agent."
        ),
        LinkCodeUseResult.INVALID: (
            "❌ *Code invalide.*\nVérifiez votre code et réessayez."
        ),
        LinkCodeUseResult.EXPIRED: (
            "⏳ *Code expiré.*\nRetournez dans SmartReply pour en générer un nouveau."
        ),
        LinkCodeUseResult.USED: (
            "⚠️ *Code déjà utilisé.*\nGénérez un nouveau code depuis SmartReply."
        ),
        LinkCodeUseResult.USER_ALREADY_CONNECTED: (
            "ℹ️ Ce compte SmartReply est déjà connecté à Telegram."
        ),
        LinkCodeUseResult.CHAT_ALREADY_CONNECTED: (
            "ℹ️ Ce chat Telegram est déjà lié à un autre compte SmartReply."
        ),
    }
    await send_telegram_message(chat_id, messages[result])


async def check_user_status(chat_id: int, db: Session) -> None:
    """Lit exclusivement la connexion Neon associée à ce chat."""
    connected = db.execute(
        select(TelegramConnection.id).where(
            TelegramConnection.telegram_chat_id == chat_id
        )
    ).scalar_one_or_none()
    if connected is not None:
        message = "📊 *Statut :* 🟢 Connecté à SmartReply Agent"
    else:
        message = "📊 *Statut :* ⚪ Non connecté. Envoyez votre code `SR-XXXXX`."
    await send_telegram_message(chat_id, message)


# ---------------------------------------------------------------------------
# IMPLÉMENTATION GOOGLE SHEETS DÉPRÉCIÉE
# Conservée pour historique et migration uniquement. Le webhook et /status ne
# l'appellent plus ; Neon est l'unique source de vérité du nouveau flux.
# ---------------------------------------------------------------------------


def _legacy_parse_dt(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return None


def _legacy_is_expired(record: dict) -> bool:
    expiration = _legacy_parse_dt(record.get("expires_at", ""))
    return expiration is not None and datetime.now(timezone.utc) > expiration


def _legacy_find_user_integration_row(sheet_integrations, user_id) -> int | None:
    records = sheet_integrations.get_all_records()
    for index, record in enumerate(records, start=2):
        if str(record.get("user_id")) == str(user_id):
            return index
    return None


async def legacy_handle_connection_code_sheet(
    chat_id: int, message: dict, code: str
) -> None:
    """DÉPRÉCIÉ — ancienne consommation non atomique dans Google Sheets."""
    try:
        workbook = get_central_workbook()
        sheet = workbook.worksheet("telegram_connection_codes")
        records = sheet.get_all_records()

        row_index = None
        target_record = None
        for index, record in enumerate(records, start=2):
            if str(record.get("connection_code", "")).strip() == code.strip():
                row_index = index
                target_record = record
                break

        if not target_record:
            await send_telegram_message(chat_id, "❌ *Code invalide.*")
            return

        status = str(target_record.get("status", "")).strip().lower()
        if status == "used":
            await send_telegram_message(chat_id, "⚠️ *Code déjà utilisé.*")
            return
        if status in ("expired", "cancelled") or _legacy_is_expired(target_record):
            if status not in ("expired", "cancelled"):
                sheet.update_cell(row_index, 4, "expired")
            await send_telegram_message(chat_id, "⏳ *Code expiré.*")
            return
        if status != "pending":
            await send_telegram_message(chat_id, "❌ *Code invalide.*")
            return

        now_string = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        telegram_user_id = message["from"]["id"]
        sheet.update_cell(row_index, 4, "used")
        sheet.update_cell(row_index, 7, now_string)
        sheet.update_cell(row_index, 8, str(telegram_user_id))
        sheet.update_cell(row_index, 9, str(chat_id))

        user_id = target_record.get("user_id")
        integrations = workbook.worksheet("Integrations")
        integration_row = _legacy_find_user_integration_row(integrations, user_id)
        if integration_row:
            integrations.update_cell(integration_row, 7, str(chat_id))
            integrations.update_cell(integration_row, 8, "connecté")

        await send_telegram_message(chat_id, "🎉 *Connexion réussie !*")
    except Exception:
        # Fonction dépréciée et non appelée ; aucun identifiant n'est journalisé.
        await send_telegram_message(chat_id, "⚠️ Vérification indisponible.")


async def legacy_check_user_status_sheet(chat_id: int) -> None:
    """DÉPRÉCIÉ — ancien statut lu dans telegram_connection_codes."""
    try:
        workbook = get_central_workbook()
        records = workbook.worksheet("telegram_connection_codes").get_all_records()
        connected = any(
            str(record.get("telegram_chat_id")) == str(chat_id)
            and record.get("status") == "used"
            for record in records
        )
        message = (
            "📊 *Statut :* 🟢 Connecté à SmartReply Agent"
            if connected
            else "📊 *Statut :* ⚪ Non connecté."
        )
        await send_telegram_message(chat_id, message)
    except Exception:
        # Fonction dépréciée et non appelée ; aucun identifiant n'est journalisé.
        await send_telegram_message(chat_id, "📊 *Statut indisponible.*")
