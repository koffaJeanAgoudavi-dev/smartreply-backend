# SmartReply Agent — Backend API

Assistant IA SaaS qui analyse, organise et prépare le traitement des emails
professionnels : réception Gmail → filtrage anti-spam → analyse Gemini →
stockage Google Sheets → brouillon Gmail → notification Telegram avec actions
**Voir / Envoyer / Ignorer**.

> Moins de temps dans votre boîte mail. Plus de temps pour votre activité.

**Statut : MVP V1 — Étapes 1 et 2 complétées ✅** (authentification,
OAuth Gmail et entreprise avec Sheet dédié)

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
- **PostgreSQL / Neon** — utilisateurs, connexions Gmail et entreprises
- **SQLAlchemy / Alembic** — accès aux données et migrations versionnées

Le pipeline historique conserve ses données métier dans Google Sheets. La couche
multi-utilisateur est stockée dans PostgreSQL sur Neon.

## 📁 Structure du projet

```
app/
├── main.py                  # Point d'entrée FastAPI + routeurs
├── core/
│   └── config.py            # Configuration (variables d'environnement)
├── db/
│   ├── database.py          # Connexion SQLAlchemy vers Neon
│   └── sheets_client.py     # Client gspread (pipeline historique)
└── modules/
    ├── auth/                # Comptes utilisateurs + JWT
    ├── gmail_oauth/         # Connexion Gmail chiffrée par utilisateur
    ├── companies/           # Entreprise unique + copie Google Sheet dédiée
    ├── telegram/            # Module 0 : connexion par codes SR-XXXXX (webhook)
    ├── gmail/               # Module 1 : réception des emails
    ├── filtering/           # Module 2 : anti-spam / anti-newsletter
    ├── ai/                  # Module 3 : analyse Gemini
    ├── sheets/              # Module 4 : écriture Sheet Data Entreprise
    ├── drafts/              # Module 5 : brouillons Gmail
    ├── notifications/       # Module 6 : notifications Telegram
    ├── actions/             # Module 7 : callbacks Envoyer / Ignorer
    └── pipeline/            # Orchestrateur (chaîne complète 1→6)
alembic/                     # Migrations PostgreSQL versionnées
scripts/
└── setup_webhook.py         # Pointe le webhook Telegram vers ce service
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
| `GOOGLE_SERVICE_ACCOUNT_JSON` | JSON du compte de service ; son `client_email` reçoit l'accès writer à chaque Sheet d'entreprise |
| `GOOGLE_DATA_SHEET_ID` | ID du Google Sheet Data Entreprise historique |
| `GOOGLE_DRIVE_CLIENT_ID` / `GOOGLE_DRIVE_CLIENT_SECRET` | Client OAuth du compte propriétaire des copies d'entreprise |
| `GOOGLE_DRIVE_REFRESH_TOKEN` | Refresh token OAuth Drive du compte propriétaire |
| `GOOGLE_DRIVE_TEMPLATE_ID` | (Optionnel) ID du template à copier ; une valeur dédiée est fournie par défaut |
| `GEMINI_API_KEY` | Clé API Google AI Studio |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Client OAuth Gmail par utilisateur |
| `GOOGLE_REFRESH_TOKEN` | Refresh token historique du pipeline mono-utilisateur |
| `DATABASE_URL` | URL PostgreSQL Neon |
| `JWT_SECRET` / `ACCESS_TOKEN_EXPIRE_MINUTES` | Signature et durée des JWT utilisateurs |
| `TOKEN_ENCRYPTION_KEY` | Clé Fernet des refresh tokens Gmail stockés dans Neon |
| `CRON_SECRET` | (Optionnel) Sécurise `POST /api/v1/pipeline/run` via l'en-tête `X-CRON-SECRET` |

3. **Webhook Telegram** : `python scripts/setup_webhook.py`
4. **Cron externe** (ex. cron-job.org) : `POST /api/v1/pipeline/run`
   toutes les 5 min avec l'en-tête `X-CRON-SECRET`

### Accès du pipeline aux Sheets d'entreprise

Chaque copie créée par `POST /api/v1/companies` reçoit deux accès `writer` :

- l'adresse de l'utilisateur SmartReply ;
- le `client_email` extrait de `GOOGLE_SERVICE_ACCOUNT_JSON`, utilisé par
  `gspread` dans le pipeline.

Si l'un des partages échoue, la nouvelle copie est supprimée et aucune ligne
`companies` n'est créée.

Pour réparer et tester gratuitement l'accès de sa propre entreprise, un
utilisateur authentifié peut appeler depuis Swagger :

```text
POST /api/v1/companies/me/pipeline-access
```

L'endpoint ajoute ou élève la permission du compte de service, puis ouvre
réellement le Sheet avec `gspread`. Il ne retourne ni `sheet_id`, ni adresse du
compte de service :

```json
{
  "pipeline_access": true,
  "permission_updated": true
}
```

`permission_updated` vaut `false` lorsque l'accès `writer` était déjà correct.

Pour un traitement global, si un shell est disponible, le script suivant reste
idempotent et ne modifie aucune ligne PostgreSQL :

```bash
python scripts/share_company_sheets_with_service_account.py
```

Pour un rattrapage manuel ponctuel : copier l'adresse du compte concerné depuis
**Google Cloud → IAM et administration → Comptes de service**, ouvrir le Sheet,
cliquer sur **Partager**, ajouter cette adresse avec le rôle **Éditeur**. Cette
adresse doit correspondre au champ `client_email` du JSON configuré sur Render.

## 🧪 Endpoints API et de validation

| Endpoint | Module | Description |
|---|---|---|
| `GET /health` | — | Santé du service |
| `POST /api/v1/auth/register` | Auth | Crée un utilisateur et renvoie son JWT |
| `POST /api/v1/auth/login` | Auth | Authentifie un utilisateur |
| `GET /api/v1/gmail/connect` | Gmail OAuth | Démarre la connexion Gmail utilisateur |
| `GET /api/v1/gmail/status` | Gmail OAuth | Retourne le statut de connexion Gmail |
| `POST /api/v1/companies` | Companies | Crée l'entreprise et sa copie de Sheet dédiée |
| `GET /api/v1/companies/me` | Companies | Retourne l'entreprise de l'utilisateur JWT |
| `POST /api/v1/companies/me/pipeline-access` | Companies | Répare et vérifie l'accès gspread du pipeline |
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
- [x] **Étape 2** — Couche multi-utilisateur ✅
  - [x] comptes utilisateurs et JWT
  - [x] OAuth2 Gmail par utilisateur
  - [x] entreprise unique et Sheet dynamique
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

