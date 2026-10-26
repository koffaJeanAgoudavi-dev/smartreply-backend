"""Création d'entreprise et gestion de sa copie Google Sheet dédiée."""

import json
import logging

import httpx
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.sheets_client import get_sheets_client
from app.modules.companies.models import Company
from app.modules.companies.schemas import CompanyCreate

logger = logging.getLogger(__name__)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
DRIVE_TOKEN_URI = "https://oauth2.googleapis.com/token"
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"


class CompanyAlreadyExistsError(ValueError):
    """L'utilisateur possède déjà son entreprise unique."""


class DriveConfigurationError(RuntimeError):
    """Les secrets OAuth Drive propriétaire ne sont pas configurés."""


class DriveOperationError(RuntimeError):
    """Une opération distante Google Drive a échoué."""


class PipelineAccessError(RuntimeError):
    """Le compte de service ne peut pas ouvrir le Sheet avec gspread."""


class CompanyPersistenceError(RuntimeError):
    """L'enregistrement PostgreSQL de l'entreprise a échoué."""


def get_service_account_email() -> str:
    """Extrait l'adresse du compte de service sans exposer son JSON."""
    raw_credentials = settings.GOOGLE_SERVICE_ACCOUNT_JSON
    if not raw_credentials:
        raise DriveConfigurationError("Compte de service Google non configuré.")

    try:
        service_account_info = json.loads(raw_credentials)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error("GOOGLE_SERVICE_ACCOUNT_JSON n'est pas un JSON valide")
        raise DriveConfigurationError(
            "Configuration du compte de service Google invalide."
        ) from exc

    if not isinstance(service_account_info, dict):
        raise DriveConfigurationError(
            "Configuration du compte de service Google invalide."
        )

    client_email = service_account_info.get("client_email")
    if not isinstance(client_email, str) or not client_email.strip():
        logger.error("client_email absent de GOOGLE_SERVICE_ACCOUNT_JSON")
        raise DriveConfigurationError("Email du compte de service Google introuvable.")
    return client_email.strip()


def _get_drive_access_token() -> str:
    """Rafraîchit un jeton OAuth du compte Google propriétaire des copies."""
    required_settings = {
        "GOOGLE_DRIVE_CLIENT_ID": settings.GOOGLE_DRIVE_CLIENT_ID,
        "GOOGLE_DRIVE_CLIENT_SECRET": settings.GOOGLE_DRIVE_CLIENT_SECRET,
        "GOOGLE_DRIVE_REFRESH_TOKEN": settings.GOOGLE_DRIVE_REFRESH_TOKEN,
    }
    missing = [name for name, value in required_settings.items() if not value]
    if missing:
        logger.error(
            "Configuration OAuth Drive incomplète (%s variable(s) manquante(s))",
            len(missing),
        )
        raise DriveConfigurationError("Intégration Google Drive non configurée.")

    credentials = Credentials(
        token=None,
        refresh_token=settings.GOOGLE_DRIVE_REFRESH_TOKEN,
        token_uri=DRIVE_TOKEN_URI,
        client_id=settings.GOOGLE_DRIVE_CLIENT_ID,
        client_secret=settings.GOOGLE_DRIVE_CLIENT_SECRET,
        scopes=[DRIVE_SCOPE],
    )
    try:
        credentials.refresh(Request())
    except GoogleAuthError as exc:
        logger.error("Échec du rafraîchissement OAuth Drive (%s)", type(exc).__name__)
        raise DriveConfigurationError(
            "Authentification Google Drive indisponible."
        ) from exc

    if not credentials.token:
        raise DriveConfigurationError("Google OAuth n'a renvoyé aucun jeton d'accès.")
    return credentials.token


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _delete_sheet_with_client(
    client: httpx.Client,
    sheet_id: str,
    headers: dict[str, str],
) -> bool:
    """Supprime une copie ; 404 signifie qu'elle est déjà absente."""
    try:
        response = client.delete(
            f"{DRIVE_API_BASE}/files/{sheet_id}",
            headers=headers,
        )
    except httpx.HTTPError as exc:
        logger.error(
            "Nettoyage Google Drive impossible pour la copie %s (%s)",
            sheet_id,
            type(exc).__name__,
        )
        return False

    if response.status_code in (204, 404):
        return True

    logger.error(
        "Nettoyage Google Drive refusé pour la copie %s (HTTP %s)",
        sheet_id,
        response.status_code,
    )
    return False


def _create_writer_permission(
    client: httpx.Client,
    sheet_id: str,
    headers: dict[str, str],
    email: str,
    *,
    send_notification: bool,
    recipient_kind: str,
) -> None:
    """Ajoute un éditeur Drive et transforme tout échec en erreur métier."""
    try:
        response = client.post(
            f"{DRIVE_API_BASE}/files/{sheet_id}/permissions",
            headers=headers,
            params={
                "sendNotificationEmail": str(send_notification).lower(),
                "fields": "id",
            },
            json={
                "type": "user",
                "role": "writer",
                "emailAddress": email,
            },
        )
    except httpx.HTTPError as exc:
        logger.error(
            "Appel permissions.create impossible pour %s (%s)",
            recipient_kind,
            type(exc).__name__,
        )
        raise DriveOperationError(
            "Le partage du Google Sheet a été interrompu."
        ) from exc

    if response.status_code not in (200, 201):
        logger.error(
            "Échec de permissions.create pour %s (HTTP %s)",
            recipient_kind,
            response.status_code,
        )
        raise DriveOperationError(
            "Impossible de partager le Google Sheet avec tous les comptes requis."
        )


def duplicate_and_share_sheet(company_name: str, user_email: str) -> str:
    """Copie le template et donne l'édition à l'utilisateur et au pipeline."""
    service_account_email = get_service_account_email()
    access_token = _get_drive_access_token()
    headers = _headers(access_token)
    template_id = settings.GOOGLE_DRIVE_TEMPLATE_ID
    if not template_id:
        raise DriveConfigurationError("Template Google Drive non configuré.")

    copy_payload = {"name": f"SmartReply Data - {company_name}"}
    copy_url = f"{DRIVE_API_BASE}/files/{template_id}/copy"

    try:
        with httpx.Client(timeout=30.0) as client:
            try:
                copy_response = client.post(
                    copy_url,
                    headers=headers,
                    params={"fields": "id,owners(emailAddress)"},
                    json=copy_payload,
                )
            except httpx.HTTPError as exc:
                logger.error("Appel files.copy impossible (%s)", type(exc).__name__)
                raise DriveOperationError(
                    "Le service Google Drive est temporairement inaccessible."
                ) from exc

            if copy_response.status_code != 200:
                logger.error(
                    "Échec de files.copy Google Drive (HTTP %s)",
                    copy_response.status_code,
                )
                raise DriveOperationError(
                    "Impossible de dupliquer le template Google Sheet."
                )

            try:
                copy_data = copy_response.json()
            except ValueError as exc:
                raise DriveOperationError(
                    "Réponse invalide reçue de Google Drive."
                ) from exc
            if not isinstance(copy_data, dict):
                raise DriveOperationError("Réponse invalide reçue de Google Drive.")
            new_sheet_id = copy_data.get("id")
            if not new_sheet_id:
                raise DriveOperationError(
                    "Google Drive n'a renvoyé aucun identifiant de copie."
                )

            # Le propriétaire OAuth possède déjà la copie. Les autres comptes
            # requis reçoivent une permission writer, sans doublon d'adresse.
            owner_emails = {
                str(owner.get("emailAddress", "")).casefold()
                for owner in copy_data.get("owners", [])
                if isinstance(owner, dict)
            }
            required_recipients = (
                (user_email, True, "l'utilisateur"),
                (service_account_email, False, "le compte de service"),
            )
            processed_emails: set[str] = set()
            try:
                for email, send_notification, recipient_kind in required_recipients:
                    normalized_email = email.casefold()
                    if (
                        normalized_email in owner_emails
                        or normalized_email in processed_emails
                    ):
                        continue
                    _create_writer_permission(
                        client,
                        new_sheet_id,
                        headers,
                        email,
                        send_notification=send_notification,
                        recipient_kind=recipient_kind,
                    )
                    processed_emails.add(normalized_email)
            except DriveOperationError:
                _delete_sheet_with_client(client, new_sheet_id, headers)
                raise

            return new_sheet_id
    except DriveOperationError:
        raise
    except httpx.HTTPError as exc:
        logger.error("Session Google Drive interrompue (%s)", type(exc).__name__)
        raise DriveOperationError(
            "Le service Google Drive est temporairement inaccessible."
        ) from exc


def delete_orphaned_sheet(sheet_id: str) -> bool:
    """Tente de supprimer une copie dont l'insertion PostgreSQL a échoué."""
    try:
        headers = _headers(_get_drive_access_token())
        with httpx.Client(timeout=15.0) as client:
            return _delete_sheet_with_client(client, sheet_id, headers)
    except (DriveConfigurationError, httpx.HTTPError) as exc:
        logger.error(
            "Suppression de la copie orpheline %s impossible (%s)",
            sheet_id,
            type(exc).__name__,
        )
        return False


def ensure_service_account_writer(sheet_id: str) -> bool:
    """Ajoute ou élève l'accès pipeline sur un Sheet existant.

    Retourne True lorsqu'une permission a été créée ou modifiée, False lorsque
    le compte de service avait déjà un rôle suffisant. Cette opération ne
    supprime jamais le Sheet : elle sert au rattrapage des données existantes.
    """
    service_account_email = get_service_account_email()
    headers = _headers(_get_drive_access_token())
    permissions_url = f"{DRIVE_API_BASE}/files/{sheet_id}/permissions"

    try:
        with httpx.Client(timeout=30.0) as client:
            try:
                response = client.get(
                    permissions_url,
                    headers=headers,
                    params={
                        "fields": "permissions(id,type,role,emailAddress)",
                    },
                )
            except httpx.HTTPError as exc:
                logger.error(
                    "Lecture des permissions Drive impossible (%s)",
                    type(exc).__name__,
                )
                raise DriveOperationError(
                    "Impossible de vérifier les permissions du Google Sheet."
                ) from exc

            if response.status_code != 200:
                logger.error(
                    "Lecture des permissions Drive refusée (HTTP %s)",
                    response.status_code,
                )
                raise DriveOperationError(
                    "Impossible de vérifier les permissions du Google Sheet."
                )

            try:
                response_data = response.json()
            except ValueError as exc:
                raise DriveOperationError(
                    "Réponse invalide reçue de Google Drive."
                ) from exc
            if not isinstance(response_data, dict):
                raise DriveOperationError("Réponse invalide reçue de Google Drive.")

            permissions = response_data.get("permissions", [])
            if not isinstance(permissions, list):
                raise DriveOperationError(
                    "Réponse de permissions invalide reçue de Google Drive."
                )

            target_email = service_account_email.casefold()
            for permission in permissions:
                if not isinstance(permission, dict):
                    continue
                permission_email = str(permission.get("emailAddress", "")).casefold()
                if permission_email != target_email:
                    continue

                if permission.get("role") in {
                    "owner",
                    "organizer",
                    "fileOrganizer",
                    "writer",
                }:
                    return False

                permission_id = permission.get("id")
                if not permission_id:
                    raise DriveOperationError(
                        "Permission Google Drive existante invalide."
                    )
                try:
                    update_response = client.patch(
                        f"{permissions_url}/{permission_id}",
                        headers=headers,
                        params={"fields": "id,role"},
                        json={"role": "writer"},
                    )
                except httpx.HTTPError as exc:
                    logger.error(
                        "Élévation de permission Drive impossible (%s)",
                        type(exc).__name__,
                    )
                    raise DriveOperationError(
                        "Impossible d'accorder l'accès au pipeline."
                    ) from exc
                if update_response.status_code != 200:
                    logger.error(
                        "Élévation de permission Drive refusée (HTTP %s)",
                        update_response.status_code,
                    )
                    raise DriveOperationError(
                        "Impossible d'accorder l'accès au pipeline."
                    )
                return True

            _create_writer_permission(
                client,
                sheet_id,
                headers,
                service_account_email,
                send_notification=False,
                recipient_kind="le compte de service",
            )
            return True
    except DriveOperationError:
        raise
    except httpx.HTTPError as exc:
        logger.error("Session Google Drive interrompue (%s)", type(exc).__name__)
        raise DriveOperationError(
            "Le service Google Drive est temporairement inaccessible."
        ) from exc


def ensure_and_verify_pipeline_access(sheet_id: str) -> bool:
    """Répare la permission puis vérifie une vraie ouverture via gspread.

    Retourne True si la permission Drive a été créée ou modifiée, False si elle
    était déjà suffisante. Le Sheet et son identifiant ne sont jamais journalisés.
    """
    permission_updated = ensure_service_account_writer(sheet_id)
    try:
        get_sheets_client().open_by_key(sheet_id)
    except Exception as exc:
        logger.error(
            "Vérification gspread du Sheet d'entreprise échouée (%s)",
            type(exc).__name__,
        )
        raise PipelineAccessError(
            "Le compte de service ne peut pas ouvrir le Google Sheet."
        ) from exc
    return permission_updated


def get_company_by_user_id(db: Session, user_id: int) -> Company | None:
    """Retourne l'entreprise unique de l'utilisateur, si elle existe."""
    return db.execute(
        select(Company).where(Company.user_id == user_id)
    ).scalar_one_or_none()


def get_company_by_sheet_id(db: Session, sheet_id: str) -> Company | None:
    """Retourne l'entreprise qui possède déjà ce Sheet, si elle existe."""
    return db.execute(
        select(Company).where(Company.sheet_id == sheet_id)
    ).scalar_one_or_none()


def create_company_record(
    db: Session,
    user_id: int,
    user_email: str,
    data: CompanyCreate,
) -> Company:
    """Crée la copie Drive puis persiste son propriétaire dans PostgreSQL."""
    if get_company_by_user_id(db, user_id) is not None:
        raise CompanyAlreadyExistsError(
            "Un utilisateur ne peut posséder qu'une seule entreprise."
        )

    sheet_id = duplicate_and_share_sheet(data.nom_entreprise, user_email)
    company = Company(
        user_id=user_id,
        nom_entreprise=data.nom_entreprise,
        secteur=data.secteur,
        sheet_id=sheet_id,
    )

    try:
        db.add(company)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing_user_company = get_company_by_user_id(db, user_id)
        existing_sheet_company = get_company_by_sheet_id(db, sheet_id)
        if existing_sheet_company is None:
            delete_orphaned_sheet(sheet_id)
        if existing_user_company is not None:
            raise CompanyAlreadyExistsError(
                "Un utilisateur ne peut posséder qu'une seule entreprise."
            ) from exc
        raise CompanyPersistenceError("Impossible d'enregistrer l'entreprise.") from exc
    except Exception as exc:
        db.rollback()
        delete_orphaned_sheet(sheet_id)
        raise CompanyPersistenceError("Impossible d'enregistrer l'entreprise.") from exc

    try:
        db.refresh(company)
    except Exception as exc:
        # Le commit est déjà durable : ne surtout pas supprimer le Sheet associé.
        logger.error("Entreprise créée mais relecture PostgreSQL impossible")
        raise CompanyPersistenceError(
            "Entreprise créée, mais sa relecture a échoué."
        ) from exc
    return company
