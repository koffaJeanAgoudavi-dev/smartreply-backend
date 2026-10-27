"""
SmartReply Agent — PIPELINE : Route de lancement
=================================================
Lance le pipeline complet sur les emails non lus.

Manuel :
  curl -X POST https://smartreply-backend-sscn.onrender.com/api/v1/pipeline/run \
       -H "X-CRON-SECRET: votre_secret"

Automatique : planificateur externe (cron-job.org) appelle ce endpoint
toutes les 5 minutes avec l'en-tête X-CRON-SECRET.
"""

import logging
import os
import traceback

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.modules.pipeline.service import run_pipeline
from app.modules.pipeline_selection.service import (
    complete_multi_tenant_simulation,
    prepare_multi_tenant_simulation,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/pipeline", tags=["Pipeline"])

# Sécurité : si CRON_SECRET est défini sur Render, l'en-tête est exigé.
# Si vide (défaut), le endpoint reste ouvert (pratique pour les tests).
CRON_SECRET = os.getenv("CRON_SECRET", "")


@router.post("/run")
async def run(request: Request, background_tasks: BackgroundTasks):
    """Lance le chemin historique ou prépare la simulation multi-tenant."""
    if CRON_SECRET and request.headers.get("X-CRON-SECRET") != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Non autorisé")

    # Interrupteur désactivé : comportement historique strictement inchangé.
    if not settings.MULTI_TENANT_PIPELINE_ENABLED:
        try:
            return await run_pipeline()
        except Exception:
            traceback.print_exc()
            raise HTTPException(
                status_code=502,
                detail="Pipeline échoué — voir logs Render",
            )

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
            complete_multi_tenant_simulation,
            prepared,
        )

    return JSONResponse(
        status_code=202,
        content=prepared.result.to_public_dict(),
    )
