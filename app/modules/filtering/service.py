"""
SmartReply Agent — MODULE 2 : Filtrage (anti-spam / anti-newsletter)
====================================================================
Règles heuristiques calquées sur la V1.5 (Make) :
  - en-tête List-Unsubscribe présent
  - expéditeur type no-reply / noreply / newsletter / notifications
  - corps contenant "unsubscribe" / "se désinscrire" / lien de désabonnement
  - mots-clés spam classiques

Décision : "traiter" (l'email entre dans le pipeline) ou "ignorer" (newsletter/spam).
L'analyse IA (Module 3) confirmera ensuite la catégorie — ce filtre est un
pré-filtre rapide et gratuit (aucun appel API).
"""

EXPEDITEURS_BLOQUES = (
    "no-reply", "noreply", "no_reply", "donotreply", "do-not-reply",
    "newsletter", "mailer", "notifications@", "notification@",
    "marketing@", "promo@", "bounce@", "mailing@",
)

MOTS_CORPS_BLOQUES = (
    "unsubscribe", "se désinscrire", "se désabonner", "désabonnement",
    "cliquez ici pour vous désabonner", "click here to unsubscribe",
    "if you no longer wish to receive", "pour ne plus recevoir",
)

MOTS_SPAM = (
    "vous avez gagné", "claim your prize", "loterie", "lottery winner",
    "héritage", "inheritance claim", "gagnez de l'argent rapidement",
    "crypto giveaway", "prince", "viagra", "pilules",
)


def should_process(expediteur: str, corps: str, headers_raw: dict | None = None) -> dict:
    """
    Retourne {"decision": "traiter"|"ignorer", "raison": str}.
    headers_raw : en-têtes bruts du message si disponibles (pour List-Unsubscribe).
    """
    exp = (expediteur or "").lower()
    body = (corps or "").lower()

    # 1. En-tête List-Unsubscribe (signature typique des newsletters)
    if headers_raw:
        for name, value in headers_raw.items():
            if name.lower() == "list-unsubscribe":
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
