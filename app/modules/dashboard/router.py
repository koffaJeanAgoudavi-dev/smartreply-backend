"""Endpoints JWT du dashboard Phase B, toujours limités à l'entreprise courante."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.companies.service import get_company_by_user_id
from app.modules.dashboard import service
from app.modules.dashboard.emails import get_company_email, list_company_emails
from app.modules.dashboard.schemas import (
    DashboardEmailItem,
    DashboardEmailsResponse,
    DashboardOverviewResponse,
    RecentEmailsResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/dashboard", tags=["Dashboard"])


def _current_company(db: Session, user_id: int):
    company = get_company_by_user_id(db, user_id=user_id)
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucune entreprise trouvée pour cet utilisateur.",
        )
    return company


def _sheet_error_type(exc: service.CompanySheetReadError) -> str:
    cause = exc.__cause__
    return type(cause).__name__ if cause is not None else type(exc).__name__


@router.get("/overview", response_model=DashboardOverviewResponse)
def dashboard_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardOverviewResponse:
    """Agrégats Neon sans contenu ni identifiant externe."""
    company = _current_company(db, current_user.id)
    return service.get_dashboard_overview(db, company_id=company.id)


@router.get("/recent-emails", response_model=RecentEmailsResponse)
def recent_emails(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RecentEmailsResponse:
    """Emails récents du seul Sheet rattaché au propriétaire JWT."""
    company = _current_company(db, current_user.id)
    try:
        emails = service.list_recent_company_emails(
            db,
            company=company,
            limit=limit,
        )
    except service.CompanySheetReadError as exc:
        logger.error(
            "Lecture dashboard du Sheet impossible (%s)",
            _sheet_error_type(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Les emails récents sont temporairement indisponibles.",
        ) from exc
    return RecentEmailsResponse(emails=emails)


@router.get("/emails", response_model=DashboardEmailsResponse)
def dashboard_emails(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    search: str | None = Query(default=None, max_length=200),
    category: str | None = Query(default=None, max_length=64),
    priority: str | None = Query(default=None, max_length=32),
    email_status: str | None = Query(default=None, alias="status", max_length=32),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardEmailsResponse:
    """Liste paginée des emails connus du système pour l'entreprise JWT."""
    company = _current_company(db, current_user.id)
    try:
        return list_company_emails(
            db,
            company=company,
            page=page,
            page_size=page_size,
            search=search,
            category=category,
            priority=priority,
            status=email_status,
        )
    except service.CompanySheetReadError as exc:
        logger.error(
            "Lecture du Sheet Emails impossible (%s)",
            _sheet_error_type(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Les emails sont temporairement indisponibles.",
        ) from exc


@router.get("/emails/{email_id}", response_model=DashboardEmailItem)
def dashboard_email_detail(
    email_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardEmailItem:
    """Détail d'un email appartenant à l'entreprise JWT."""
    company = _current_company(db, current_user.id)
    try:
        item = get_company_email(db, company=company, email_id=email_id)
    except service.CompanySheetReadError as exc:
        logger.error(
            "Lecture du Sheet Emails impossible (%s)",
            _sheet_error_type(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Les emails sont temporairement indisponibles.",
        ) from exc
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email introuvable.",
        )
    return item
