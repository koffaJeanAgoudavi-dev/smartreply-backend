"""
SmartReply Agent — MODULE 5 : Route de test isolée
===================================================
Crée un vrai brouillon dans Gmail avec un contenu factice et le
consigne dans l'onglet Brouillons (méthode §9bis).

Test :
  curl -X POST https://smartreply-backend-sscn.onrender.com/api/v1/drafts/test
"""
import traceback
from fastapi import APIRouter, HTTPException
from app.modules.drafts.service import create_gmail_draft, record_draft_in_sheet

router = APIRouter(prefix="/api/v1/drafts", tags=["Brouillons Gmail"])

EMAIL_FACTICE = {
    "destinataire": "marie@atelierdupont.fr",
    "objet": "Re: Demande de devis — refonte site web",
    "corps": "Bonjour Marie,\n\nMerci pour votre message. Nous préparons votre devis détaillé et revenons vers vous rapidement.\n\nCordialement,\n[Votre nom]",
    "email_id": "EM-TEST-001",
}


@router.post("/test")
async def test_brouillon():
    """Crée un brouillon Gmail factice + ligne dans Brouillons."""
    try:
        draft_id = await create_gmail_draft(
            EMAIL_FACTICE["destinataire"],
            EMAIL_FACTICE["objet"],
            EMAIL_FACTICE["corps"],
        )
        ligne = record_draft_in_sheet(draft_id, EMAIL_FACTICE["email_id"], EMAIL_FACTICE["corps"])
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="Création brouillon échouée — voir logs Render")

    return {
        "message": "Brouillon créé dans Gmail ✅ et consigné dans l'onglet Brouillons ✅",
        "brouillon_id": draft_id,
        "ligne_brouillons": ligne,
        "lien": f"https://mail.google.com/mail/u/0/#drafts/{draft_id}",
    }
