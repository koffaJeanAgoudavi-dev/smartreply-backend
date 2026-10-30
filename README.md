# SmartReply Agent — Backend API

Assistant IA SaaS qui analyse, organise et prépare le traitement des emails
professionnels : réception Gmail → filtrage anti-spam → analyse Gemini →
stockage Google Sheets → brouillon Gmail → notification Telegram avec actions
**Voir / Envoyer / Ignorer**.

> Moins de temps dans votre boîte mail. Plus de temps pour votre activité.

**Statut : MVP V1 multi-utilisateur opérationnel ✅** (authentification,
OAuth Gmail, entreprise avec Sheet dédié et pipeline isolé)

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
- **PostgreSQL / Neon** — utilisateurs, connexions Gmail, entreprises et liaisons Telegram
- **SQLAlchemy / Alembic** — accès aux données et migrations versionnées

Le registre d'idempotence et les intégrations multi-utilisateur sont stockés
dans PostgreSQL sur Neon. Chaque entreprise conserve ses données métier dans
son propre Google Sheet.

## 📁 Structure du projet

```
app/
├── main.py                  # Point d'entrée FastAPI + routeurs + montage /dashboard
├── dashboard/               # Dashboard Phase B : HTML/CSS/JS statiques, sans build
├── core/
│   └── config.py            # Configuration (variables d'environnement)
├── db/
│   ├── database.py          # Connexion SQLAlchemy vers Neon
│   └── sheets_client.py     # Client gspread des Sheets d'entreprise
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
    ├── pipeline/            # Point d'entrée cron protégé
    ├── pipeline_selection/  # Sélection, allowlist, équité et verrou
    ├── pipeline_execution/  # Flux réel isolé Gmail → Sheet → brouillon
    ├── processed_emails/    # Dédoublonnage et tentatives dans Neon
    └── dashboard/           # Agrégats tenant-scoped, activité et emails récents
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
| `TELEGRAM_WEBHOOK_SECRET` | Secret obligatoire du webhook, vérifié à chaque update en mode fail-closed |
| `BASE_URL` | URL publique du service (ex. `https://xxx.onrender.com`) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | JSON du compte de service ; son `client_email` reçoit l'accès writer à chaque Sheet d'entreprise |
| `GOOGLE_DRIVE_CLIENT_ID` / `GOOGLE_DRIVE_CLIENT_SECRET` | Client OAuth du compte propriétaire des copies d'entreprise |
| `GOOGLE_DRIVE_REFRESH_TOKEN` | Refresh token OAuth Drive du compte propriétaire |
| `GOOGLE_DRIVE_TEMPLATE_ID` | (Optionnel) ID du template à copier ; une valeur dédiée est fournie par défaut |
| `GEMINI_API_KEY` | Clé API Google AI Studio |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Client OAuth Gmail par utilisateur |
| `DATABASE_URL` | URL PostgreSQL Neon |
| `INVITATION_CODES` | Codes autorisant l'inscription, séparés par des virgules ; vide ou absent = inscriptions fermées |
| `JWT_SECRET` / `ACCESS_TOKEN_EXPIRE_MINUTES` | Signature et durée des JWT utilisateurs |
| `TOKEN_ENCRYPTION_KEY` | Clé Fernet des refresh tokens Gmail stockés dans Neon |
| `PIPELINE_ALLOWED_COMPANY_IDS` | IDs internes autorisés au traitement réel, séparés par des virgules ; vide ou absent = aucune entreprise traitée |
| `MAX_EMAILS_PER_COMPANY_PER_RUN` | Plafond par entreprise et par passage (défaut : `10`) |
| `PIPELINE_RUN_TIME_BUDGET_SECONDS` | Budget total d'un passage (défaut : `240` secondes) |
| `PIPELINE_PROCESSING_STALE_SECONDS` | Délai avant reprise atomique d'une réservation `processing` ou vérification d'un envoi `sending` abandonné (défaut : `900` secondes) |
| `GEMINI_MAX_EMAIL_BODY_CHARS` | Taille maximale du corps transmis à Gemini dans le flux multi-tenant (défaut : `6000`) |
| `CRON_SECRET` | Secret obligatoire de `POST /api/v1/pipeline/run`, transmis via `X-CRON-SECRET` ; vide = requête refusée |

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

### Liaison d'un compte Telegram

Les codes et connexions Telegram sont stockés exclusivement dans Neon :

- `telegram_link_codes` conserve les codes `SR-XXXXX`, leur expiration à
  15 minutes et leur statut `pending`, `used` ou `expired` ;
- `telegram_connections` garantit une seule liaison par utilisateur et une
  seule liaison par chat Telegram.

Un utilisateur authentifié génère son code avec
`POST /api/v1/telegram/link-code`, puis envoie `/start SR-XXXXX` ou le code seul
au bot. La consommation est atomique et le statut JWT n'expose jamais le
`chat_id`. `/status` dans le bot lit également Neon.

Les éventuels onglets historiques restent dans les Sheets existants, mais le
code ne les lit et ne les modifie plus. La liaison Telegram s'appuie uniquement
sur Neon. Les anciens boutons reçoivent une réponse indiquant qu'ils ne sont
plus actifs, sans action Gmail ni Sheet.

### Dashboard Phases A et B

FastAPI sert directement le dashboard à l'adresse `/dashboard` sur la même
origine que l'API. L'interface utilise uniquement HTML, CSS et JavaScript
natifs : aucune dépendance front-end, police distante ou étape de build.

La Phase A comprend :

- inscription protégée par un code de `INVITATION_CODES`, connexion et
  déconnexion avec JWT conservé dans `localStorage` ;
- création de l'entreprise et accès à son Google Sheet ;
- accueil sans données fictives, avec le nombre réel d'intégrations connectées ;
- connexion Gmail, suivi Telegram par code temporaire et statuts réels ;
- navigation mobile repliable et interface responsive.

La Phase B ajoute des données réelles, sans backfill historique fictif :

- `GET /api/v1/dashboard/overview` calcule, dans Neon et uniquement pour
  l'entreprise du JWT, les emails analysés (`done + skipped`), traités (`done`),
  ignorés (`skipped`), les brouillons en attente (`draft_status = pending`),
  le volume des 14 derniers jours, les catégories, les priorités et l'activité ;
- `GET /api/v1/dashboard/recent-emails` ouvre en lecture seule l'onglet
  `Emails` du Sheet de cette même entreprise. Chaque `EM-<id>` doit appartenir
  au registre Neon du tenant avant affichage ; le nom expéditeur est nettoyé et
  aucune adresse, résumé, contenu, ID interne ou brouillon n'est retourné ;
- `company_activity_events` conserve uniquement la référence interne, le type
  d'une transition effectivement réussie et son horodatage. Une contrainte
  `(processed_email_id, event_type)` empêche les doublons ;
- le dashboard gère séparément les états chargement, vide, erreur et données,
  afin qu'une panne du Sheet n'empêche pas l'affichage des agrégats Neon.

La migration `b7d9e3f1a5c2` ajoute les colonnes nullable `category`, `priority`
et `received_at` à `processed_emails`, le statut final `excluded`, ainsi que la
table d'activité. Elle ne modifie aucun onglet Google Sheets et ne reconstitue
aucun historique : les événements commencent après son application.

Après le consentement Google, `/api/v1/gmail/callback` enregistre toujours le
refresh token chiffré côté serveur, puis redirige le navigateur vers
`/dashboard/?gmail=connected#integrations`. Le dashboard recharge ensuite le
statut Gmail ; aucun token Google n'est envoyé au navigateur.

#### Checklist de test manuel du dashboard

- [ ] Ouvrir `/dashboard` sur ordinateur puis sur un écran mobile étroit ;
      vérifier le menu repliable et l'absence de débordement horizontal.
- [ ] Créer un compte avec une adresse inédite, un code d'invitation autorisé
      et un mot de passe d'au moins huit caractères ; vérifier l'arrivée sur
      la création d'entreprise et le refus clair d'un code invalide.
- [ ] Créer l'entreprise avec son nom et son secteur ; vérifier les messages
      français en cas de réponse Google `502` ou `503`.
- [ ] Vérifier le nom d'entreprise dans la barre latérale et ouvrir le Sheet
      réel depuis la carte Google Sheets.
- [ ] Cliquer sur « Connecter Gmail », accepter le consentement Google et
      vérifier le retour automatique sur Intégrations avec Gmail connecté.
- [ ] Générer un code Telegram, envoyer la commande `/start …` au bot sans
      partager le code, puis vérifier le passage automatique à « Connecté ».
- [ ] Vérifier l'indicateur `3/3`, puis les quatre cartes et les graphiques
      après au moins un email `done` ou `skipped`.
- [ ] Vérifier qu'un Sheet vide affiche un état vide et qu'un Sheet inaccessible
      affiche une erreur locale sans masquer les statistiques Neon.
- [ ] Vérifier que les emails récents ne montrent aucune adresse expéditeur,
      aucun résumé, contenu, brouillon ou identifiant technique.
- [ ] Se déconnecter, se reconnecter et vérifier qu'un JWT invalide ou expiré
      renvoie vers la connexion.
- [ ] Vérifier qu'un compte sans historique reste à zéro, sans statistiques,
      emails ou activités factices.

### Pipeline multi-entreprises — Étape 3, PR 1 à PR 3

La migration de fondations `e1a5c7d9b3f2` ajoute le statut Gmail et les dates de
coupure/équité. La migration PR 2 `a4f6c8d2e1b7` ajoute `processed_emails`,
registre isolé par `company_id` avec unicité `(company_id, gmail_message_id)`,
statut `processing`, `done`, `skipped`, `excluded` ou `failed`, nombre de
tentatives, ID du brouillon Gmail nullable et code d'erreur court. Le statut
`excluded` distingue un message antérieur à l'activation, jamais soumis au
filtre, d'un message `skipped` réellement analysé puis ignoré.

Le endpoint exécute exclusivement le pipeline multi-utilisateur. Une entreprise
reste éligible seulement si elle possède un Sheet, une connexion Telegram et
une connexion Gmail `connected`. Seules les entreprises dont l'ID interne est
dans `PIPELINE_ALLOWED_COMPANY_IDS` sont traitées réellement ; toutes les
autres restent comptées en simulation. Une allowlist vide signifie donc zéro
traitement réel.

Au premier passage réel, `pipeline_activated_at` est enregistré et validé en
base **avant** le premier accès Gmail. Gmail est ensuite interrogé avec
`in:inbox after:<epoch>`, puis `internalDate` est contrôlé strictement : seuls
les messages reçus après l'activation sont admissibles. Le flux ne modifie plus
le statut lu/non lu de Gmail.

Chaque message est réservé atomiquement dans `processed_emails` avant son
traitement. Un message `done`, `skipped` ou `excluded` n'est jamais retraité.
Un email `excluded` n'alimente ni les indicateurs, ni les graphiques, ni
l'activité du dashboard. Un échec est réessayé jusqu'à trois tentatives. Une
réservation restée `processing` au-delà de `PIPELINE_PROCESSING_STALE_SECONDS`
est reprise par la même transition
atomique, sans dépasser ce plafond. Un HTTP `429` ne consomme aucune tentative
et arrête seulement l'entreprise concernée pour le passage. Un `invalid_grant`
passe sa connexion Gmail à `revoked`. Chaque entreprise reste dans son propre
bloc d'erreurs.

Le flux réel utilise explicitement le refresh token déchiffré et le `sheet_id`
de l'entreprise. Il filtre le message, tronque le corps avant Gemini et crée le
brouillon Gmail dans le même fil avec le token de cette entreprise. Son ID est
enregistré dans `processed_emails` immédiatement après la création, avant toute
écriture suivante dans le Sheet. Un nouvel essai consulte d'abord cet ID, puis
l'éventuelle ligne `Brouillons`, et ne recrée donc pas le brouillon après une
panne Sheet. Les écritures Sheet `Emails` et `Brouillons` sont idempotentes par
`Email_ID` et conservent la double écriture. La PR 2 seule ne déclenchait
encore aucune notification Telegram.

La migration PR 3 `f2c4d6e8a0b1` ajoute au registre le jeton opaque des boutons,
l'état du brouillon, `notified_at`, le compteur de notification limité à trois,
le blocage Telegram et la synchronisation du statut Sheet. Elle ajoute aussi le
marqueur d'avis de révocation à `gmail_connections`. Après la double écriture,
le bon chat reçoit le résumé et un unique bouton **Voir le brouillon**. Un échec
Telegram ne rouvre jamais le traitement Gmail/Gemini : seule la notification
est reprise. Un HTTP `403` arrête définitivement ses reprises.

Les callbacks actifs ont le format `mt_<action>_<jeton opaque>` et restent sous
la limite Telegram de 64 octets. Le jeton ne contient aucun ID métier. Le
webhook résout à chaque clic le chat et l'utilisateur Telegram, leur entreprise,
puis le brouillon de cette même entreprise. Tout autre format reçoit une réponse
polie indiquant que le bouton n'est plus actif, sans action Gmail ni Sheet.

**Voir** relit le brouillon dans le Sheet de l'entreprise et tronque le message
sous 4096 caractères. **Envoyer** réserve atomiquement l'action dans Neon,
envoie le brouillon avec le token Gmail de cette entreprise, puis synchronise
`Emails` et `Brouillons` à `Envoyé`. **Ignorer** réserve l'état `Ignoré` et
synchronise les deux onglets sans supprimer le brouillon Gmail. Les doubles
clics et rejeux ne répètent jamais l'envoi ; une synchronisation Sheet échouée
peut être rejouée sans répéter l'action Gmail. Si un état `sending` dépasse
`PIPELINE_PROCESSING_STALE_SECONDS`, le clic suivant vérifie d'abord l'existence
du brouillon dans Gmail. Un `404` confirme qu'il a déjà été consommé : Neon et
le Sheet passent à `Envoyé` sans appeler une seconde fois `drafts.send`.

Un `invalid_grant` déclenche au plus un avis Telegram avec un lien vers
`BASE_URL/dashboard/#integrations`. Le marqueur est remis à zéro uniquement
lors d'une nouvelle connexion Gmail réussie.

Le `202` est rendu après la sélection et expose seulement les IDs internes et
les compteurs d'entreprises/plafonds. `processing_company_ids` est
l'intersection éligible/allowlist ; les compteurs `processed`, `skipped` et
`failed` définitifs sont journalisés à la fin du travail d'arrière-plan, sans
contenu d'email, adresse, token, Sheet ID ou identifiant Telegram.

Le passage est protégé par `pg_try_advisory_xact_lock`, verrou PostgreSQL limité
à la transaction. Contrairement à un verrou advisory de session, il reste
compatible avec le pooler transactionnel de Neon et est libéré par rollback à
la fin du travail en arrière-plan.

#### Test réel PR 2 après déploiement

1. Récupérer l'ID interne de l'entreprise de test sans le publier.
2. Définir `PIPELINE_ALLOWED_COMPANY_IDS` avec uniquement cet ID.
3. Lancer une première fois `POST /api/v1/pipeline/run` avec le header cron :
   ce passage doit fixer
   `pipeline_activated_at` et ne doit normalement créer aucun brouillon.
4. Envoyer ensuite un nouvel email à la boîte Gmail liée.
5. Lancer un second passage : vérifier un seul brouillon dans le même fil, une
   ligne unique dans `Emails`, une ligne liée dans `Brouillons`, et aucune
   écriture dans le Sheet ou la boîte d'une autre entreprise.
6. Vérifier que la réponse et les logs ne contiennent que des IDs internes,
   statuts opérationnels et compteurs. Ne jamais copier de secret ou de contenu
   d'email dans un rapport de test.

#### Test réel PR 3 après déploiement

1. Relier deux comptes de test A et B à deux chats Telegram, deux boîtes Gmail
   et deux Sheets distincts ; placer seulement leurs IDs internes dans
   `PIPELINE_ALLOWED_COMPANY_IDS` sans les publier.
2. Envoyer un nouvel email à A puis lancer le pipeline. Vérifier une seule
   notification dans le chat A, aucune dans B, et un bouton **Voir le
   brouillon** sans ID Gmail/Sheet/email visible dans son `callback_data`.
3. Cliquer **Voir**, puis **Envoyer** deux fois rapidement. Vérifier un seul
   email envoyé, les deux statuts Sheet à `Envoyé` et la réponse polie au rejeu.
4. En environnement de test uniquement, reproduire un arrêt ambigu : consommer
   le brouillon dans Gmail tout en laissant sa ligne Neon à `sending`, attendre
   `PIPELINE_PROCESSING_STALE_SECONDS`, puis recliquer **Envoyer**. Vérifier que
   Gmail n'envoie aucun second email et que Neon puis le Sheet passent à
   `sent`/`Envoyé`.
5. Refaire avec **Ignorer** : vérifier les deux statuts `Ignoré` et la présence
   inchangée du brouillon dans Gmail.
6. Depuis le chat B, rejouer un callback capturé pour A. Vérifier une réponse
   générique et aucune lecture, écriture ou action dans Gmail/Sheet de A.
7. Provoquer temporairement un échec Telegram : au passage suivant, vérifier la
   seule reprise de notification, sans nouvel appel Gemini ni nouveau brouillon.
   Un blocage du bot (`403`) ne doit produire aucune reprise supplémentaire.
8. Révoquer Gmail : vérifier un seul avis avec le lien vers Intégrations, aucun
   second avis avant reconnexion, puis la remise à zéro après reconnexion.
9. Rejouer un callback historique et confirmer la réponse « bouton inactif »,
   sans appel Gmail ni Sheet. Vérifier enfin que réponses et logs ne contiennent
   ni adresse, contenu, jeton, Sheet ID ou identifiant Telegram.

## 🧪 Endpoints API et de validation

| Endpoint | Module | Description |
|---|---|---|
| `GET /health` | — | Santé du service |
| `GET /dashboard` | Dashboard | Ouvre l'interface Phase A (redirection vers `/dashboard/`) |
| `POST /api/v1/auth/register` | Auth | Crée un utilisateur avec un code d'invitation et renvoie son JWT |
| `POST /api/v1/auth/login` | Auth | Authentifie un utilisateur |
| `GET /api/v1/gmail/connect` | Gmail OAuth | Démarre la connexion Gmail utilisateur |
| `GET /api/v1/gmail/status` | Gmail OAuth | Retourne le statut de connexion Gmail |
| `POST /api/v1/telegram/link-code` | Telegram | Génère un code temporaire JWT de 15 minutes |
| `GET /api/v1/telegram/status` | Telegram | Retourne le statut de liaison sans exposer le chat |
| `POST /api/v1/telegram/webhook` | Telegram | Reçoit commandes, codes et callbacks avec secret |
| `POST /api/v1/companies` | Companies | Crée l'entreprise et sa copie de Sheet dédiée |
| `GET /api/v1/companies/me` | Companies | Retourne l'entreprise de l'utilisateur JWT |
| `POST /api/v1/companies/me/pipeline-access` | Companies | Répare et vérifie l'accès gspread du pipeline |
| `POST /api/v1/pipeline/run` | Pipeline | Plan sûr `202`, puis traitement multi-utilisateur allowlisté en arrière-plan |

## 📊 Statuts (figés par le cadrage)

- **Emails** : `Nouveau` → `Analysé` → `Brouillon prêt` → `Envoyé` / `Ignoré`
- **Brouillons** : `En attente` → `Envoyé` / `Ignoré`

## 🗺️ Roadmap

- [x] **Étape 1** — Première version du pipeline ✅
- [x] **Étape 2** — Couche multi-utilisateur ✅
  - [x] comptes utilisateurs et JWT
  - [x] OAuth2 Gmail par utilisateur
  - [x] entreprise unique et Sheet dynamique
  - [x] dashboard Phase A : onboarding et intégrations
- [x] **Étape 3** — Pipeline multi-utilisateur permanent
  - [x] PR 1 : sélection, plafonds, équité, verrou et simulation en lecture seule
  - [x] PR 2 : traitement réel isolé par entreprise, dédoublonnage et coupure
  - [x] PR 3 : notifications et actions Telegram isolées par entreprise
- [ ] Post-MVP : contexte entreprise dans le prompt IA, dashboard avancé,
      fallback multi-modèles

## 🔒 Sécurité

- Inscriptions fermées par défaut : `INVITATION_CODES` doit contenir au moins
  un code ; chaque candidat est comparé en temps constant et n'est jamais
  journalisé ni renvoyé par l'API
- Format recommandé des invitations : 8 caractères parmi `A-Z` hors `I`, `L`,
  `O`, et les chiffres `2-9`. La saisie et la configuration sont normalisées
  avant comparaison (espaces et tirets retirés, majuscules). Le format n'est
  pas validé séparément : seule l'appartenance à la liste autorisée compte
- Webhook Telegram refusé si `TELEGRAM_WEBHOOK_SECRET` est vide, absent du
  header `X-Telegram-Bot-Api-Secret-Token` ou incorrect
- Endpoint pipeline refusé si `CRON_SECRET` est vide, absent du header
  `X-CRON-SECRET` ou incorrect
- Aucune clé en dur dans le code : 100 % variables d'environnement
- ⚠️ Avant mise en production : régénérer les tokens de test,
  restreindre les scopes OAuth au strict nécessaire

## 📄 Licence & crédits

Produit de **DIGICRAFT Labs** — Product Owner : KOFFA JEAN AGOUDAVI.
Cadrage MVP V1 validé point par point (septembre 2026).
```

