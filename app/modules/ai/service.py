"""
SmartReply Agent — MODULE 3 : Analyse IA (Groq)
================================================
Analyse un email entrant et produit les 4 sorties validées (§5.1) :
  - urgence (haute / moyenne / basse)
  - categorie (devis, support, facturation, partenariat, spam, newsletter, autre)
  - resume
  - brouillon de réponse

Mode JSON forcé : la réponse de Groq est du JSON structuré, fiable à parser.
Le modèle est configurable via la variable d'environnement GROQ_MODEL
(les modèles Groq changent au fil du temps — liste via GET /openai/v1/models).
"""
import json
import os
import httpx
from app.core.config import settings

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

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
    Lève une exception avec le détail si l'API Groq échoue.
    """
    if not settings.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY non configurée sur Render")

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
        if resp.status_code != 200:
            # Le corps de l'erreur Groq apparaît dans les logs Render
            raise RuntimeError(f"Groq HTTP {resp.status_code}: {resp.text}")
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    return json.loads(content)
