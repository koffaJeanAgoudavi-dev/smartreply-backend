"""Endpoints JWT du dashboard Phase B, toujours limités à l'entreprise courante."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.companies.operational_status import is_company_suspended
from app.modules.companies.service import get_company_by_user_id
from app.modules.dashboard import notices, service
from app.modules.dashboard.drafts import (
    DraftActionConflictError,
    get_company_draft,
    ignore_company_draft,
    list_company_drafts,
)
from app.modules.dashboard.emails import get_company_email, list_company_emails
from app.modules.dashboard.schemas import (
    CompanyOperationalStatusResponse,
    DashboardDraftDetail,
    DashboardDraftIgnoreResponse,
    DashboardDraftsResponse,
    DashboardEmailItem,
    DashboardEmailsResponse,
    DashboardNoticeBannerResponse,
    DashboardNoticeReadResponse,
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


@router.get(
    "/operational-status",
    response_model=CompanyOperationalStatusResponse,
)
def company_operational_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanyOperationalStatusResponse:
    company = _current_company(db, current_user.id)
    suspended = is_company_suspended(company)
    return CompanyOperationalStatusResponse(
        operational_status="suspended" if suspended else "active",
        suspension_reason=company.suspension_reason if suspended else None,
        suspended_at=company.suspended_at if suspended else None,
        suspended_until=company.suspended_until if suspended else None,
    )


@router.get("/notices", response_model=DashboardNoticeBannerResponse)
def dashboard_notice_banner(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardNoticeBannerResponse:
    company = _current_company(db, current_user.id)
    result = notices.get_latest_unread_company_notice(
        db,
        company_id=company.id,
    )
    return DashboardNoticeBannerResponse.model_validate(result)


@router.post(
    "/notices/{notice_ref}/read",
    response_model=DashboardNoticeReadResponse,
)
def dashboard_mark_notice_read(
    notice_ref: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardNoticeReadResponse:
    company = _current_company(db, current_user.id)
    prefix, separator, raw_id = notice_ref.partition("-")
    if (
        prefix != "INFO"
        or separator != "-"
        or not raw_id.isdecimal()
        or len(raw_id) > 19
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Information introuvable.",
        )
    message_id = int(raw_id)
    if message_id < 1 or message_id > 2_147_483_647:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Information introuvable.",
        )
    result = notices.mark_company_notice_read(
        db,
        company_id=company.id,
        message_id=message_id,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Information introuvable.",
        )
    return DashboardNoticeReadResponse.model_validate(result)


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


@router.get("/drafts", response_model=DashboardDraftsResponse)
def dashboard_drafts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    search: str | None = Query(default=None, max_length=200),
    draft_status: str | None = Query(
        default=None,
        alias="status",
        max_length=32,
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardDraftsResponse:
    """Brouillons rapprochés avec Neon et le Sheet du seul tenant JWT."""
    company = _current_company(db, current_user.id)
    try:
        return list_company_drafts(
            db,
            company=company,
            page=page,
            page_size=page_size,
            search=search,
            status=draft_status,
        )
    except service.CompanySheetReadError as exc:
        logger.error(
            "Lecture des brouillons impossible (%s)",
            _sheet_error_type(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Les brouillons sont temporairement indisponibles.",
        ) from exc


@router.get("/drafts/{draft_ref}", response_model=DashboardDraftDetail)
def dashboard_draft_detail(
    draft_ref: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardDraftDetail:
    """Détail en lecture seule, sans identifiant Gmail dans la réponse."""
    company = _current_company(db, current_user.id)
    try:
        item = get_company_draft(
            db,
            company=company,
            draft_ref=draft_ref,
        )
    except service.CompanySheetReadError as exc:
        logger.error(
            "Lecture du détail brouillon impossible (%s)",
            _sheet_error_type(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Le brouillon est temporairement indisponible.",
        ) from exc
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Brouillon introuvable.",
        )
    return item


@router.post(
    "/drafts/{draft_ref}/ignore",
    response_model=DashboardDraftIgnoreResponse,
)
def dashboard_ignore_draft(
    draft_ref: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardDraftIgnoreResponse:
    """Ignore côté SmartReply sans envoyer ni supprimer le brouillon Gmail."""
    company = _current_company(db, current_user.id)
    try:
        result = ignore_company_draft(
            db,
            company=company,
            draft_ref=draft_ref,
        )
    except DraftActionConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ce brouillon ne peut plus être ignoré.",
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Brouillon introuvable.",
        )
    return result
