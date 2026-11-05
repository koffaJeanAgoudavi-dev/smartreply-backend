"""API interne en lecture seule du centre de pilotage SmartReply."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.modules.admin import service
from app.modules.admin.dependencies import get_current_admin
from app.modules.admin.schemas import (
    AdminCompanyDetailResponse,
    AdminCompanyListResponse,
    AdminIdentityResponse,
    AdminIncidentListResponse,
    AdminOverviewResponse,
)
from app.modules.auth.models import User

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


@router.get("/me", response_model=AdminIdentityResponse)
def admin_identity(
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> AdminIdentityResponse:
    return AdminIdentityResponse(
        user_id=current_admin.id,
        email=current_admin.email,
    )


@router.get("/overview", response_model=AdminOverviewResponse)
def admin_overview(
    _: Annotated[User, Depends(get_current_admin)],
    db: Session = Depends(get_db),
) -> AdminOverviewResponse:
    return AdminOverviewResponse.model_validate(service.get_overview(db))


@router.get("/companies", response_model=AdminCompanyListResponse)
def admin_companies(
    _: Annotated[User, Depends(get_current_admin)],
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    search: str | None = Query(default=None, max_length=255),
    validation_status: Literal["pending", "validated"] | None = Query(
        default=None
    ),
    configuration_status: Literal["ready", "incomplete"] | None = Query(
        default=None
    ),
    telegram_connected: bool | None = Query(default=None),
) -> AdminCompanyListResponse:
    result = service.list_companies(
        db,
        page=page,
        page_size=page_size,
        search=search,
        validation_status=validation_status,
        configuration_status=configuration_status,
        telegram_connected=telegram_connected,
    )
    return AdminCompanyListResponse.model_validate(result)


@router.get(
    "/companies/{company_id}",
    response_model=AdminCompanyDetailResponse,
)
def admin_company_detail(
    company_id: int,
    _: Annotated[User, Depends(get_current_admin)],
    db: Session = Depends(get_db),
) -> AdminCompanyDetailResponse:
    if company_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entreprise introuvable",
        )
    result = service.get_company_detail(db, company_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entreprise introuvable",
        )
    return AdminCompanyDetailResponse.model_validate(result)


@router.get("/incidents", response_model=AdminIncidentListResponse)
def admin_incidents(
    _: Annotated[User, Depends(get_current_admin)],
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    company_id: int | None = Query(default=None, ge=1),
    severity: Literal["critical", "warning"] | None = Query(default=None),
) -> AdminIncidentListResponse:
    result = service.list_incidents(
        db,
        page=page,
        page_size=page_size,
        company_id=company_id,
        severity=severity,
    )
    return AdminIncidentListResponse.model_validate(result)
