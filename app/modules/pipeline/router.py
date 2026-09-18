"""
SmartReply Agent — PIPELINE : Route de lancement
=================================================
Lance le pipeline complet sur les emails non lus.

Test :
  curl -X POST https://smartreply-backend-sscn.onrender.com/api/v1/pipeline/run
"""
import traceback
from fastapi import APIRouter, HTTPException
from app.modules.pipeline.service import run_pipeline

router = APIRouter(prefix="/api/v1/pipeline", tags=["Pipeline"])


@router.post("/run")
async def run():
    """Exécute le pipeline complet (Modules 1→6)."""
    try:
        return await run_pipeline()
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="Pipeline échoué — voir logs Render")
