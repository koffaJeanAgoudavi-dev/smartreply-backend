"""
SmartReply Agent — MODULE 2 : Filtrage (anti-spam / anti-newsletter)
====================================================================
  - en-tête List-Unsubscribe présent  ← SIGNAL PRINCIPAL (Zapier,
    newsletters, tous les expéditeurs en masse l'envoient)
  - expéditeur type no-reply / noreply / newsletter / notifications
  - corps contenant un lien de désabonnement (toutes variantes)
  - mots-clés spam classiques

Garde-fou en amont du pipeline : si Gemini classe quand même en
newsletter/spam, l'email est ignoré après analyse (voir pipeline).
"""

EXPEDITEURS_BLOQUES = (
    "no-reply", "noreply", "no_reply", "donotreply", "do-not-reply",
    "newsletter", "mailer", "notifications@", "notification@",
    "marketing@", "promo@", "bounce@", "mailing@",
)

MOTS_CORPS_BLOQUES = (
    "unsubscribe",
    "désinscrire",      # couvre "me / se / nous désinscrire"
    "désabonner",       # couvre "se / Me désabonner"
    "désabonnement",
    "pour ne plus recevoir",
    "if you no longer wish to receive",
    "click here to unsubscribe",
    "email preferences",
    "préférences de communication",
    "manage your preferences",
    "update your preferences",
    "view in browser",
    "voir en ligne",
)

MOTS_SPAM = (
    "vous avez gagné", "claim your prize", "loterie", "lottery winner",
    "héritage", "inheritance claim", "gagnez de l'argent rapidement",
    "crypto giveaway", "viagra", "pilules",
)


def should_process(expediteur: str, corps: str, list_unsubscribe: str = "") -> dict:
    """
    Retourne {"decision": "traiter"|"ignorer", "raison": str}.
    list_unsubscribe : valeur de l'en-tête (chaîne vide si absent).
    """
    exp = (expediteur or "").lower()
    body = (corps or "").lower()

    # 1. En-tête List-Unsubscribe — signal fiable des emails en masse
    if list_unsubscribe:
        return {"decision": "ignorer", "raison": "newsletter (en-tête List-Unsubscribe)"}

    # 2. Expéditeur automatique / marketing
    for motif in EXPEDITEURS_BLOQUES:
        if motif in exp:
            return {"decision": "ignorer", "raison": f"expéditeur automatique ({motif})"}

    # 3. Corps contenant un lien de désabonnement
    for motif in MOTS_CORPS_BLOQUES:
        if motif in body:
            return {"decision": "ignorer", "raison": f"newsletter (lien désabonnement : '{motif}')"}

    # 4. Mots-clés spam
    for motif in MOTS_SPAM:
        if motif in body:
            return {"decision": "ignorer", "raison": f"spam suspect ('{motif}')"}

    return {"decision": "traiter", "raison": "email professionnel à analyser"}
