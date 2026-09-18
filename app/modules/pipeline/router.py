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
import os
import traceback
from fastapi import APIRouter, HTTPException, Request
from app.modules.pipeline.service import run_pipeline

router = APIRouter(prefix="/api/v1/pipeline", tags=["Pipeline"])

# Sécurité : si CRON_SECRET est défini sur Render, l'en-tête est exigé.
# Si vide (défaut), le endpoint reste ouvert (pratique pour les tests).
CRON_SECRET = os.getenv("CRON_SECRET", "")


@router.post("/run")
async def run(request: Request):
    """Exécute le pipeline complet (Modules 1→6)."""
    if CRON_SECRET and request.headers.get("X-CRON-SECRET") != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Non autorisé")
    try:
        return await run_pipeline()
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="Pipeline échoué — voir logs Render")
