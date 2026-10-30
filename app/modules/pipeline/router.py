"""Endpoint protégé du pipeline multi-utilisateur."""

import hmac
import logging
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

from app.modules.pipeline_execution.service import complete_multi_tenant_run
from app.modules.pipeline_selection.service import prepare_multi_tenant_simulation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/pipeline", tags=["Pipeline"])

CRON_SECRET = os.getenv("CRON_SECRET", "")


def _cron_request_is_authorized(request: Request) -> bool:
    """Refuse toute exécution si le secret serveur ou son en-tête manque."""
    provided_secret = request.headers.get("X-CRON-SECRET", "")
    return bool(CRON_SECRET) and hmac.compare_digest(provided_secret, CRON_SECRET)


@router.post("/run")
async def run(request: Request, background_tasks: BackgroundTasks):
    """Prépare un passage multi-utilisateur et lance son traitement en arrière-plan."""
    if not _cron_request_is_authorized(request):
        raise HTTPException(status_code=401, detail="Non autorisé")

    try:
        prepared = prepare_multi_tenant_simulation()
    except Exception as exc:
        # Le type suffit au diagnostic sans exposer de donnée ni de secret.
        logger.error(
            "Préparation de la simulation multi-tenant impossible (%s)",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="Simulation multi-tenant temporairement indisponible",
        ) from exc

    if prepared.lock_acquired:
        background_tasks.add_task(
            complete_multi_tenant_run,
            prepared,
        )

    return JSONResponse(
        status_code=202,
        content=prepared.result.to_public_dict(),
    )
