"""
SmartReply Agent — MODULE 3 : Analyse IA (Groq)
================================================
Analyse un email entrant et produit les 4 sorties validées (§5.1) :
  - urgence (haute / moyenne / basse)
  - categorie (devis, support, facturation, partenariat, spam, newsletter, autre)
  - resume
  - brouillon de réponse

Mode JSON forcé : la réponse de Groq est du JSON structuré, fiable à parser.
"""
import json
import httpx
from app.core.config import settings

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"

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
    Envoie l'email à Groq et retourne l'analyse structurée.
    Lève une exception si l'API Groq échoue (le caller gère le fallback).
    """
    user_content = (
        f"Expéditeur : {expediteur}\n"
        f"Objet : {objet}\n\n"
        f"Corps du message :\n{corps}"
    )

    headers = {
        "Authorization": f"Bearer {settings.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.3,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(GROQ_API_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    return json.loads(content)
