"""API interne en lecture seule du centre de pilotage SmartReply."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.client_ip import ClientAddressError, get_client_ip
from app.core.config import settings
from app.core.rate_limit import (
    RedisRateLimiter,
    enforce_rate_limit,
    get_rate_limiter,
)
from app.db.database import get_db
from app.modules.admin import service
from app.modules.admin.dependencies import (
    get_allowlisted_admin,
    get_current_admin,
    optional_admin_session,
)
from app.modules.admin.schemas import (
    AdminAccessStatusResponse,
    AdminCompanyDetailResponse,
    AdminCompanyListResponse,
    AdminIdentityResponse,
    AdminIncidentListResponse,
    AdminMessageCreate,
    AdminMessageResponse,
    AdminOverviewResponse,
    AdminSuspensionCreate,
    AdminSuspensionResponse,
    AdminUnlockRequest,
    AdminUnlockResponse,
)
from app.modules.admin.security import (
    ADMIN_SESSION_COOKIE,
    ADMIN_SESSION_DURATION_SECONDS,
    AdminSecurityConfigurationError,
    create_admin_session_token,
    verify_admin_access_code,
)
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


def _admin_cookie_is_secure() -> bool:
    return settings.ENVIRONMENT.strip().lower() == "production"


def _unlock_client_ip(request: Request) -> str:
    try:
        return get_client_ip(request)
    except ClientAddressError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Protection anti-abus temporairement indisponible",
        ) from exc


@router.get("/access", response_model=AdminAccessStatusResponse)
def admin_access_status(
    request: Request,
    current_admin: Annotated[User, Depends(get_allowlisted_admin)],
) -> AdminAccessStatusResponse:
    session = optional_admin_session(request, current_admin)
    return AdminAccessStatusResponse(
        user_id=current_admin.id,
        email=current_admin.email,
        unlocked=session is not None,
        expires_at=session.expires_at if session is not None else None,
    )


@router.post("/unlock", response_model=AdminUnlockResponse)
def admin_unlock(
    payload: AdminUnlockRequest,
    request: Request,
    response: Response,
    current_admin: Annotated[User, Depends(get_allowlisted_admin)],
    limiter: RedisRateLimiter = Depends(get_rate_limiter),
) -> AdminUnlockResponse:
    client_ip = _unlock_client_ip(request)
    enforce_rate_limit(
        limiter,
        limiter.admin_unlock_buckets(client_ip, current_admin.id),
    )
    try:
        valid_code = verify_admin_access_code(payload.code.get_secret_value())
    except AdminSecurityConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configuration du code administrateur indisponible",
        ) from exc
    if not valid_code:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Code administrateur invalide",
        )

    try:
        token, expires_at = create_admin_session_token(current_admin.id)
    except AdminSecurityConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configuration du code administrateur indisponible",
        ) from exc
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=token,
        max_age=ADMIN_SESSION_DURATION_SECONDS,
        path="/api/v1/admin",
        secure=_admin_cookie_is_secure(),
        httponly=True,
        samesite="strict",
    )
    return AdminUnlockResponse(expires_at=expires_at)


@router.post("/lock", status_code=status.HTTP_204_NO_CONTENT)
def admin_lock(
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    del current_user
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        key=ADMIN_SESSION_COOKIE,
        path="/api/v1/admin",
        secure=_admin_cookie_is_secure(),
        httponly=True,
        samesite="strict",
    )
    return response


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


@router.post(
    "/companies/{company_id}/messages",
    response_model=AdminMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def admin_send_company_message(
    company_id: int,
    payload: AdminMessageCreate,
    current_admin: Annotated[User, Depends(get_current_admin)],
    db: Session = Depends(get_db),
) -> AdminMessageResponse:
    try:
        message = service.send_company_information_message(
            db,
            company_id=company_id,
            actor_user_id=current_admin.id,
            title=payload.title,
            body=payload.body,
        )
    except service.AdminCompanyNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entreprise introuvable",
        ) from exc
    except service.AdminActionPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Impossible d’enregistrer le message",
        ) from exc
    return AdminMessageResponse(
        message_ref=f"INFO-{message.id}",
        company_id=message.company_id,
        title=message.title,
        created_at=message.created_at,
    )


@router.put(
    "/companies/{company_id}/suspension",
    response_model=AdminSuspensionResponse,
)
def admin_suspend_company(
    company_id: int,
    payload: AdminSuspensionCreate,
    current_admin: Annotated[User, Depends(get_current_admin)],
    db: Session = Depends(get_db),
) -> AdminSuspensionResponse:
    try:
        result = service.suspend_company(
            db,
            company_id=company_id,
            actor_user_id=current_admin.id,
            reason=payload.reason,
            suspended_until=payload.suspended_until,
        )
    except service.AdminCompanyNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entreprise introuvable",
        ) from exc
    except service.AdminActionConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except service.AdminActionPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Impossible de suspendre l’entreprise",
        ) from exc
    return AdminSuspensionResponse.model_validate(result)


@router.delete(
    "/companies/{company_id}/suspension",
    response_model=AdminSuspensionResponse,
)
def admin_resume_company(
    company_id: int,
    current_admin: Annotated[User, Depends(get_current_admin)],
    db: Session = Depends(get_db),
) -> AdminSuspensionResponse:
    try:
        result = service.resume_company(
            db,
            company_id=company_id,
            actor_user_id=current_admin.id,
        )
    except service.AdminCompanyNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entreprise introuvable",
        ) from exc
    except service.AdminActionConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except service.AdminActionPersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Impossible de réactiver l’entreprise",
        ) from exc
    return AdminSuspensionResponse.model_validate(result)


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
