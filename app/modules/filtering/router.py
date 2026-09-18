"""
SmartReply Agent — MODULE 2 : Route de test isolée
===================================================
Teste le filtrage sur les VRAIS emails non lus du compte (Module 1 branché
en entrée — validation "tester avec Module 1" de la méthode §9bis).

Test :
  curl https://smartreply-backend-sscn.onrender.com/api/v1/filter/test
"""
import traceback
from fastapi import APIRouter, HTTPException
from app.modules.gmail.service import list_unread_emails
from app.modules.filtering.service import should_process

router = APIRouter(prefix="/api/v1/filter", tags=["Filtrage"])


@router.get("/test")
async def test_filtrage():
    """Lecture des non-lus + décision traiter/ignorer pour chacun."""
    try:
        emails = await list_unread_emails(max_results=10)
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="Lecture Gmail échouée — voir logs Render")

    resultats = []
    for e in emails:
        decision = should_process(e["expediteur"], e["corps"] or e["snippet"])
        resultats.append({
            "id_gmail": e["id_gmail"],
            "expediteur": e["expediteur"],
            "objet": e["objet"],
            **decision,
        })

    return {
        "nb_emails": len(resultats),
        "nb_a_traiter": sum(1 for r in resultats if r["decision"] == "traiter"),
        "nb_ignores": sum(1 for r in resultats if r["decision"] == "ignorer"),
        "resultats": resultats,
    }
