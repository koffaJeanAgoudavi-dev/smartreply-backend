"""Endpoints JWT du dashboard Phase B, toujours limités à l'entreprise courante."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.modules.auth.models import User
from app.modules.companies.router import get_current_user
from app.modules.companies.service import get_company_by_user_id
from app.modules.dashboard import service
from app.modules.dashboard.schemas import (
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
        error_type = (
            type(exc.__cause__).__name__
            if exc.__cause__ is not None
            else type(exc).__name__
        )
        logger.error(
            "Lecture dashboard du Sheet impossible (%s)",
            error_type,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Les emails récents sont temporairement indisponibles.",
        ) from exc
    return RecentEmailsResponse(emails=emails)
