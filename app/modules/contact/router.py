"""Endpoint public du formulaire de contact."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.client_ip import ClientAddressError, get_client_ip
from app.core.rate_limit import (
    RedisRateLimiter,
    enforce_rate_limit,
    get_rate_limiter,
)
from app.modules.contact.schemas import ContactRequest, ContactResponse
from app.modules.contact.service import ContactDeliveryError, deliver_contact

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Contact"])


@router.post("/contact", response_model=ContactResponse)
async def submit_contact(
    payload: ContactRequest,
    request: Request,
    limiter: RedisRateLimiter = Depends(get_rate_limiter),
) -> ContactResponse:
    """Transmet une demande publique uniquement après contrôle anti-abus."""

    try:
        client_ip = get_client_ip(request)
    except ClientAddressError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Le formulaire est temporairement indisponible.",
        ) from exc
    enforce_rate_limit(limiter, limiter.contact_buckets(client_ip))

    try:
        await deliver_contact(payload)
    except ContactDeliveryError as exc:
        logger.warning("Échec de livraison du formulaire de contact (%s)", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Votre message n’a pas pu être envoyé. "
                "Veuillez réessayer dans quelques instants."
            ),
        ) from exc
    return ContactResponse()
