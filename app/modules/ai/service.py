"""
SmartReply Agent — MODULE 3 : Analyse IA (Gemini)
=================================================
Analyse un email entrant et produit les 4 sorties validées (§5.1) :
  - urgence (haute / moyenne / basse)
  - categorie (devis, support, facturation, partenariat, spam, newsletter, autre)
  - resume
  - brouillon de réponse

Sortie JSON forcée via responseMimeType (fiable à parser).
Modèle configurable via GEMINI_MODEL (défaut : gemini-2.5-flash).
"""
import json
import os
import httpx
from app.core.config import settings

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

SYSTEM_PROMPT = """Tu es l'analyseur d'emails de SmartReply Agent, un assistant
pour entrepreneurs et petites entreprises.

Tu reçois un email professionnel entrant et tu produis UNIQUEMENT un objet JSON
avec exactement ces 4 clés :

- "urgence" : "haute" | "moyenne" | "basse"
- "categorie" : "devis" | "support" | "facturation" | "partenariat" | "spam" | "newsletter" | "autre"
- "resume" : résumé en 1-2 phrases, en français
- "brouillon" : brouillon de réponse professionnel, poli et concis, en français,
  prêt à être envoyé (tu peux utiliser des champs génériques comme [Votre nom])

Règles :
- urgente = demande de devis, problème client, deadline imminente, réclamation
- spam/newsletter = publicité, notifications automatiques, no-reply marketing
- Le brouillon ne doit JAMAIS s'engager ferme sur des prix ou des dates précises
- Réponds UNIQUEMENT avec le JSON, sans texte avant ni après"""


async def analyze_email(expediteur: str, objet: str, corps: str) -> dict:
    """
    Envoie l'email à Gemini et retourne l'analyse structurée.
    Lève une exception avec le détail si l'API échoue.
    """
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY non configurée sur Render")

    user_content = (
        f"Expéditeur : {expediteur}\n"
        f"Objet : {objet}\n\n"
        f"Corps du message :\n{corps}"
    )

    url = f"{GEMINI_API_URL}/{GEMINI_MODEL}:generateContent"
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.3,
        },
    }
    params = {"key": settings.GEMINI_API_KEY}

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, params=params, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text}")
        data = resp.json()

    content = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(content)
