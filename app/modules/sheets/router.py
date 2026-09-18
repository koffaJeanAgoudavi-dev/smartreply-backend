"""
SmartReply Agent — MODULE 4 : Route de test isolée
===================================================
Teste l'écriture dans le Sheet Data Entreprise avec une analyse factice
(méthode §9bis : testable seul avant d'être branché sur les Modules 1-3).

Test :
  curl -X POST https://smartreply-backend-sscn.onrender.com/api/v1/sheets/test
"""
import traceback
from fastapi import APIRouter, HTTPException
from app.modules.sheets.service import append_analyzed_email

router = APIRouter(prefix="/api/v1/sheets", tags=["Google Sheets"])

ANALYSE_FACTICE = {
    "expediteur": "contact@client-exemple.fr",
    "objet": "Demande de devis — refonte site web",
    "resume": "Client souhaitant un devis pour refonte de site vitrine avant fin octobre.",
    "categorie": "devis",
    "priorite": "haute",
}


@router.post("/test")
async def test_stockage():
    """Écrit une ligne d'analyse factice dans l'onglet Emails."""
    try:
        result = append_analyzed_email(**ANALYSE_FACTICE)
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="Écriture Sheet échouée — voir logs Render")

    return {
        "message": "Ligne écrite dans l'onglet Emails ✅",
        **result,
    }
