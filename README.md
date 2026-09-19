# SmartReply Agent — Backend API

Assistant IA SaaS qui analyse, organise et prépare le traitement des emails
professionnels : réception Gmail → filtrage anti-spam → analyse Gemini →
stockage Google Sheets → brouillon Gmail → notification Telegram avec actions
**Voir / Envoyer / Ignorer**.

> Moins de temps dans votre boîte mail. Plus de temps pour votre activité.

**Statut : MVP V1 — Étape 1 complétée ✅** (pipeline mono-utilisateur,
déployé et autonome)

---

## 🏗️ Architecture

Reproduction fidèle du pipeline validé en V1.5, recodé en Python / FastAPI :

```
Gmail ──→ Module 1 (Réception) ──→ Module 2 (Filtrage)
   ──→ Module 3 (Analyse Gemini) ──→ Module 4 (Sheet "Emails")
   ──→ Module 5 (Brouillon Gmail + Sheet "Brouillons")
   ──→ Module 6 (Notification Telegram) ──→ Module 7 (Callbacks Envoyer/Ignorer)
```

| Module | Rôle | Statut |
|---|---|---|
| 0 | Compte, onboarding, connexion Telegram par code SR-XXXXX | ✅ |
| 1 | Réception des emails (Gmail API) | ✅ |
| 2 | Filtrage anti-spam / anti-newsletter | ✅ |
| 3 | Analyse IA (Gemini) : urgence, catégorie, résumé, brouillon | ✅ |
| 4 | Écriture dans le Google Sheet Data Entreprise | ✅ |
| 5 | Création du brouillon Gmail + double écriture | ✅ |
| 6 | Notification Telegram + bouton « Voir le brouillon » | ✅ |
| 7 | Callbacks Telegram : Envoyer / Ignorer + mise à jour des statuts | ✅ |
| — | Déclenchement automatique (cron externe, toutes les 5 min) | ✅ |

## 🧰 Stack technique

- **FastAPI** (Python 3) — API REST asynchrone
- **Gmail API** — réception, création et envoi de brouillons (OAuth2 refresh token)
- **Google Sheets API** — stockage (compte de service + `gspread`)
- **Telegram Bot API** — notifications + callbacks (webhook sécurisé par secret token)
- **Google Gemini** — analyse IA des emails (sortie JSON structurée)

Aucune base de données : le stockage repose sur Google Sheets (cadrage MVP).

## 📁 Structure du projet

```
app/
├── main.py                  # Point d'entrée FastAPI + routeurs
├── core/
│   └── config.py            # Configuration (variables d'environnement)
├── db/
│   └── sheets_client.py     # Client gspread (compte de service)
└── modules/
    ├── telegram/            # Module 0 : connexion par codes SR-XXXXX (webhook)
    ├── gmail/               # Module 1 : réception des emails
    ├── filtering/           # Module 2 : anti-spam / anti-newsletter
    ├── ai/                  # Module 3 : analyse Gemini
    ├── sheets/              # Module 4 : écriture Sheet Data Entreprise
    ├── drafts/              # Module 5 : brouillons Gmail
    ├── notifications/       # Module 6 : notifications Telegram
    ├── actions/             # Module 7 : callbacks Envoyer / Ignorer
    └── pipeline/            # Orchestrateur (chaîne complète 1→6)
scripts/
├── setup_webhook.py         # Pointe le webhook Telegram vers ce service
└── test_sheets_connection.py # Diagnostic connexion Google Sheets
```

## 🚀 Déploiement (Render)

1. **Push** sur `main` → Render déploie automatiquement (`procfile` :
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT`)
2. **Variables d'environnement** (dashboard Render) :

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token du bot Telegram (@BotFather) |
| `TELEGRAM_WEBHOOK_SECRET` | Secret du webhook (vérifié à chaque update) |
| `BASE_URL` | URL publique du service (ex. `https://xxx.onrender.com`) |
| `GOOGLE_SHEET_ID` | ID du Google Sheet central |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | JSON complet du compte de service Google |
| `GOOGLE_DATA_SHEET_ID` | ID du Google Sheet Data Entreprise |
| `GEMINI_API_KEY` | Clé API Google AI Studio |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN` | OAuth2 Gmail (compte unique, Étape 1) |
| `CRON_SECRET` | (Optionnel) Sécurise `POST /api/v1/pipeline/run` via l'en-tête `X-CRON-SECRET` |

3. **Webhook Telegram** : `python scripts/setup_webhook.py`
4. **Cron externe** (ex. cron-job.org) : `POST /api/v1/pipeline/run`
   toutes les 5 min avec l'en-tête `X-CRON-SECRET`

## 🧪 Endpoints de test (validation module par module)

| Endpoint | Module | Description |
|---|---|---|
| `GET /health` | — | Santé du service |
| `POST /api/v1/ai/test` | 3 | Analyse Gemini d'un email factice |
| `GET /api/v1/gmail/test` | 1 | Liste les emails non lus |
| `GET /api/v1/filter/test` | 1+2 | Filtre les emails non lus (traiter/ignorer) |
| `POST /api/v1/sheets/test` | 4 | Écrit une ligne d'analyse factice |
| `POST /api/v1/drafts/test` | 5 | Crée un brouillon Gmail factice |
| `POST /api/v1/notifications/test` | 6 | Envoie une notification Telegram |
| `POST /api/v1/actions/test-ignore` | 7 | Simule un clic « Ignorer » |
| `POST /api/v1/actions/test-send` | 7 | Crée et envoie un vrai brouillon de test |
| `POST /api/v1/pipeline/run` | 1→6 | **Pipeline complet** (manuel ou via cron) |

## 📊 Statuts (figés par le cadrage)

- **Emails** : `Nouveau` → `Analysé` → `Brouillon prêt` → `Envoyé` / `Ignoré`
- **Brouillons** : `En attente` → `Envoyé` / `Ignoré`

## 🗺️ Roadmap

- [x] **Étape 1** — Pipeline en code, mono-utilisateur (compte en dur) ✅
- [ ] **Étape 2** — Couche multi-utilisateur : comptes, OAuth2 Gmail réel
      par utilisateur, Sheets dynamiques par entreprise
- [ ] **Étape 3** — Branchement du pipeline sur la couche multi-utilisateur
- [ ] Post-MVP : contexte entreprise dans le prompt IA, dashboard web,
      fallback multi-modèles

## 🔒 Sécurité

- Webhook Telegram vérifié par `X-Telegram-Bot-Api-Secret-Token`
- Endpoint pipeline protégé par `X-CRON-SCRIPT` (si `CRON_SECRET` défini)
- Aucune clé en dur dans le code : 100 % variables d'environnement
- ⚠️ Avant mise en production : régénérer les tokens de test,
  restreindre les scopes OAuth au strict nécessaire

## 📄 Licence & crédits

Produit de **DIGICRAFT Labs** — Product Owner : KOFFA JEAN AGOUDAVI.
Cadrage MVP V1 validé point par point (septembre 2026).
```

