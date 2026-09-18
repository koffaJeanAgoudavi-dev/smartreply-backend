"""
SmartReply Agent — MODULE 1 : Route de test isolée
===================================================
Endpoint de test pour valider la réception Gmail seule (méthode §9bis) :
lit les emails NON LUS du compte configuré et les retourne en JSON.

Test :
  curl https://smartreply-backend-sscn.onrender.com/api/v1/gmail/test
"""
import traceback
from fastapi import APIRouter, HTTPException
from app.modules.gmail.service import list_unread_emails

router = APIRouter(prefix="/api/v1/gmail", tags=["Gmail"])


@router.get("/test")
async def test_reception():
    """Réception Module 1 : liste les emails non lus (test isolé)."""
    try:
        emails = await list_unread_emails(max_results=5)
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="La lecture Gmail a échoué — voir logs Render")

    return {
        "nb_emails_non_lus": len(emails),
        "emails": emails,
    }
