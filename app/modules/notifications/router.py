"""
SmartReply Agent — MODULE 6 : Route de test isolée
===================================================
Envoie une notification de test (analyse factice + brouillon factice)
au compte Telegram connecté (méthode §9bis).

Test :
  curl -X POST https://smartreply-backend-sscn.onrender.com/api/v1/notifications/test
"""
import traceback
from fastapi import APIRouter, HTTPException
from app.modules.notifications.service import send_email_notification

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications Telegram"])

# Étape 1 : compte unique en dur (Étape 3 : lira l'onglet Integrations dynamiquement)
CHAT_ID_TEST = 6599553668

ANALYSE_FACTICE = {
    "expediteur": "Marie Dupont <marie@atelierdupont.fr>",
    "objet": "Demande de devis — refonte site web",
    "resume": "Cliente souhaitant un devis pour refonte de site vitrine avant mi-novembre.",
    "categorie": "devis",
        "priorite": "haute",
    "email_id": "EM-TEST-001",
    "draft_id": "r-1234567890",  # fictif pour le test ; réel en production (Module 5)
}


@router.post("/test")
async def test_notification():
    """Envoie la notification factice sur Telegram."""
    try:
        result = await send_email_notification(chat_id=CHAT_ID_TEST, **ANALYSE_FACTICE)
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="Envoi Telegram échoué — voir logs Render")

    return {
        "message": "Notification Telegram envoyée ✅",
        "telegram_message_id": result["message_id"],
    }
