"""Endpoints JWT de l'entreprise unique de l'utilisateur connecté."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.company_allowlist import get_company_validation_status
from app.db.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.companies import service
from app.modules.companies.schemas import (
    CompanyCreate,
    CompanyResponse,
    CompanySettingsUpdate,
    CompanyValidationStatusResponse,
    PipelineAccessResponse,
)
from app.modules.drive_oauth import service as drive_oauth_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/companies", tags=["Companies"])


@router.post("", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
def create_company(
    data: CompanyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanyResponse:
    """Crée l'entreprise et son Sheet directement dans le Drive du client."""
    try:
        return service.create_company_record(
            db=db,
            user_id=current_user.id,
            user_email=current_user.email,
            data=data,
        )
    except service.CompanyAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except drive_oauth_service.DriveConnectionRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail=str(exc),
        ) from exc
    except drive_oauth_service.DriveAuthorizationRevokedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except (
        service.DriveConfigurationError,
        drive_oauth_service.DriveOAuthConfigurationError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (service.DriveOperationError, service.PipelineAccessError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except service.CompanyPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.error("Création d'entreprise interrompue (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne du serveur",
        ) from exc


@router.get("/me", response_model=CompanyResponse)
def get_my_company(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanyResponse:
    """Retourne l'entreprise de l'utilisateur connecté, ou 404."""
    company = service.get_company_by_user_id(db, user_id=current_user.id)
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucune entreprise trouvée pour cet utilisateur.",
        )
    return company


@router.patch("/me/settings", response_model=CompanyResponse)
def update_my_company_settings(
    data: CompanySettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanyResponse:
    """Modifie le secteur admissible et le ton, jamais le nom ni le statut."""
    company = service.get_company_by_user_id(db, user_id=current_user.id)
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucune entreprise trouvée pour cet utilisateur.",
        )
    try:
        return service.update_company_settings(db, company, data)
    except service.SectorUpdateLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        logger.error("Configuration de l'allowlist pipeline invalide")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La modification du secteur est temporairement indisponible.",
        ) from exc
    except service.CompanyPersistenceError as exc:
        logger.error("Enregistrement des paramètres d'entreprise impossible")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Impossible d'enregistrer les paramètres de l'entreprise.",
        ) from exc


@router.get(
    "/me/validation-status",
    response_model=CompanyValidationStatusResponse,
)
def get_my_company_validation_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanyValidationStatusResponse:
    """Dérive EN ATTENTE/VALIDÉE de l'allowlist utilisée par le pipeline."""
    company = service.get_company_by_user_id(db, user_id=current_user.id)
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucune entreprise trouvée pour cet utilisateur.",
        )
    try:
        validation_status = get_company_validation_status(company.id)
    except ValueError as exc:
        logger.error("Configuration de l'allowlist pipeline invalide")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Le statut de validation est temporairement indisponible.",
        ) from exc
    return CompanyValidationStatusResponse(validation_status=validation_status)


@router.post("/me/pipeline-access", response_model=PipelineAccessResponse)
def ensure_my_pipeline_access(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PipelineAccessResponse:
    """Répare l'accès du compte de service et le vérifie réellement via gspread."""
    company = service.get_company_by_user_id(db, user_id=current_user.id)
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucune entreprise trouvée pour cet utilisateur.",
        )

    try:
        access_token = None
        if company.sheet_owner_mode == "client":
            access_token = drive_oauth_service.get_user_access_token(
                db,
                current_user.id,
            )
        if access_token is None:
            permission_updated = service.ensure_and_verify_pipeline_access(
                company.sheet_id
            )
        else:
            permission_updated = service.ensure_and_verify_pipeline_access(
                company.sheet_id,
                access_token=access_token,
            )
    except drive_oauth_service.DriveConnectionRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail=str(exc),
        ) from exc
    except drive_oauth_service.DriveAuthorizationRevokedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except (
        service.DriveConfigurationError,
        drive_oauth_service.DriveOAuthConfigurationError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (service.DriveOperationError, service.PipelineAccessError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return PipelineAccessResponse(
        pipeline_access=True,
        permission_updated=permission_updated,
    )
