"""Endpoints JWT de l'entreprise unique de l'utilisateur connecté."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.companies import service
from app.modules.companies.schemas import (
    CompanyCreate,
    CompanyResponse,
    PipelineAccessResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/companies", tags=["Companies"])


@router.post("", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
def create_company(
    data: CompanyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanyResponse:
    """Crée l'entreprise, sa copie du template et son partage utilisateur."""
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
    except service.DriveConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except service.DriveOperationError as exc:
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
        permission_updated = service.ensure_and_verify_pipeline_access(company.sheet_id)
    except service.DriveConfigurationError as exc:
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
