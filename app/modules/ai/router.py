"""
SmartReply Agent — MODULE 3 : Route de test isolée
===================================================
Endpoint de test pour valider le Module 3 seul (méthode §9bis) :
un email factice en dur si aucun corps n'est fourni, ou l'email de votre choix.

Test avec curl :
  curl -X POST https://smartreply-backend-sscn.onrender.com/api/v1/ai/test
  curl -X POST https://smartreply-backend-sscn.onrender.com/api/v1/ai/test \
       -H "Content-Type: application/json" \
       -d '{"expediteur": "client@test.fr", "objet": "Devis site web",
            "corps": "Bonjour, je souhaite un devis pour..."}'
"""
import traceback
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.modules.ai.service import analyze_email

router = APIRouter(prefix="/api/v1/ai", tags=["Analyse IA"])

EMAIL_FACTICE = {
    "expediteur": "contact@client-exemple.fr",
    "objet": "Demande de devis — refonte site web",
    "corps": (
        "Bonjour,\n\n"
        "Je suis Marie Dupont, gérante de la boutique Atelier Dupont. "
        "Je souhaite faire refaire le site web de ma boutique avant la période "
        "des fêtes (idéalement mi-novembre). Pouvez-vous m'envoyer un devis "
        "détaillé avec vos disponibilités ?\n\n"
        "Merci d'avance,\nMarie"
    ),
}


class EmailInput(BaseModel):
    expediteur: str | None = None
    objet: str | None = None
    corps: str | None = None


@router.post("/test")
async def test_analyze(email: EmailInput | None = None):
    """Analyse un email (factice par défaut) et retourne le JSON Groq."""
    src = EMAIL_FACTICE if email is None or email.objet is None else email
    try:
        analysis = await analyze_email(src["expediteur"], src["objet"], src["corps"])
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="L'analyse Groq a échoué — voir logs Render")

    return {
        "email_teste": {"expediteur": src["expediteur"], "objet": src["objet"]},
        "analyse": analysis,
    }
