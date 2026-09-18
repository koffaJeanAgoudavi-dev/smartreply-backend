"""
SmartReply Agent — MODULE 7 : Routes de test isolées
=====================================================
Teste les actions sans passer par un clic Telegram réel (méthode §9bis).

  curl -X POST https://smartreply-backend-sscn.onrender.com/api/v1/actions/test-ignore
  curl -X POST https://smartreply-backend-sscn.onrender.com/api/v1/actions/test-send
"""
import traceback
from fastapi import APIRouter, HTTPException
from app.modules.actions.service import send_draft, update_statuts
from app.modules.drafts.service import create_gmail_draft, record_draft_in_sheet

router = APIRouter(prefix="/api/v1/actions", tags=["Actions"])

# ← MODIFIEZ : votre PROPRE adresse Gmail (vous recevrez le test dans votre boîte)
DESTINATAIRE_TEST = "votre-email@gmail.com"


@router.post("/test-ignore")
async def test_ignore():
    """Simule un clic « Ignorer » (ids factices ; vérifiez vos onglets)."""
    try:
        update_statuts("EM-TEST-001", "r-1234567890", "Ignoré")
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="Mise à jour échouée — voir logs Render")
    return {"message": "Statuts « Ignoré » appliqués à EM-TEST-001 / r-1234567890 (si ces lignes existent dans vos onglets)"}


@router.post("/test-send")
async def test_send():
    """Crée un vrai brouillon vers VOTRE adresse, l'envoie, statut « Envoyé »."""
    try:
        corps = "Ceci est un test du pipeline d'envoi SmartReply Agent."
        draft_id = await create_gmail_draft(DESTINATAIRE_TEST, "✅ Test SmartReply Agent", corps)
        ligne = record_draft_in_sheet(draft_id, "EM-TEST-SEND", corps)
        await send_draft(draft_id)
        update_statuts("EM-TEST-SEND", draft_id, "Envoyé")
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="Test d'envoi échoué — voir logs Render")

    return {
        "message": "Brouillon créé, envoyé à vous-même, statut « Envoyé » ✅",
        "brouillon_id": draft_id,
        "ligne_brouillons": ligne,
    }
