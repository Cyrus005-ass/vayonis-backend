# Vayonis Backend V1

API FastAPI pour la publication multi-réseaux (Instagram, Facebook, LinkedIn) avec OAuth, stockage S3 et tâches asynchrones via Celery.

## Stack technique

- **Runtime** : Python 3.12, FastAPI, Uvicorn
- **Base de données** : PostgreSQL 16 + SQLAlchemy 2.0 + Alembic
- **Cache / Broker** : Redis 7
- **Auth** : JWT (python-jose) + bcrypt + chiffrement Fernet des tokens
- **Stockage** : S3-compatible (Cloudflare R2, AWS S3, MinIO)
- **Workers** : Celery + Celery Beat
- **Clients HTTP** : httpx

## Démarrage rapide

```bash
cp .env.example .env

# Générer une clé Fernet valide et la copier dans .env :
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up -d db redis
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Services disponibles :

- API : `http://localhost:8000`
- Healthcheck : `http://localhost:8000/health`

## Variables d'environnement

### Obligatoires

| Variable | Description |
|---|---|
| `DATABASE_URL` | URL PostgreSQL (ex: `postgresql://vayonis:vayonis@localhost:5432/vayonis`) |
| `REDIS_URL` | URL Redis (ex: `redis://localhost:6379/0`) |
| `SECRET_KEY` | Clé secrète pour signer les JWT |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Durée de vie des JWT (défaut : `60`) |
| `TOKEN_ENCRYPTION_KEY` | Clé Fernet (32 bytes base64) pour chiffrer les tokens OAuth |

### OAuth

| Variable | Plateforme |
|---|---|
| `META_APP_ID` / `META_APP_SECRET` / `META_REDIRECT_URI` | Facebook & Instagram (Meta Graph API v21.0) |
| `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` / `INSTAGRAM_REDIRECT_URI` | Instagram standalone |
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` / `LINKEDIN_REDIRECT_URI` | LinkedIn |

### Stockage S3

| Variable | Description |
|---|---|
| `S3_ENDPOINT_URL` | URL du endpoint S3-compatible |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | Clés d'accès |
| `S3_BUCKET_NAME` | Nom du bucket (défaut : `vayonis-media`) |
| `S3_REGION` | Région (défaut : `auto`) |
| `S3_PUBLIC_BASE_URL` | URL publique du bucket (optionnel) |

### Feature flags

| Variable | Défaut | Description |
|---|---|---|
| `ENABLE_AI` | `false` | Fonctionnalités IA |
| `ENABLE_ANALYTICS` | `false` | Analytics |
| `ENABLE_BILLING` | `false` | Facturation |
| `ENABLE_TIKTOK` | `false` | Intégration TikTok |
| `ENABLE_YOUTUBE` | `false` | Intégration YouTube |

## Architecture du projet

```
app/
  api/v1/
    routes/
      auth.py              # Register, login, Google OAuth
      users.py             # Profil utilisateur
      social_accounts.py   # Connexion OAuth (Meta, Instagram, LinkedIn)
      posts.py             # CRUD posts, upload media, cibles, publication
  core/
    config.py             # Paramètres (pydantic-settings)
    database.py           # Moteur SQLAlchemy + Session
    deps.py               # Dépendances FastAPI (get_current_user)
    security.py           # JWT + chiffrement Fernet
  models/
    user.py               # Utilisateur (email, password, google_id, onboarding)
    social_account.py     # Compte social (tokens chiffrés, metadata)
    media_asset.py        # Fichier média uploadé
    post.py               # Post (caption, content_type, scheduled_at, status)
    post_media.py         # Association post <-> media (sort_order)
    post_target.py        # Cible de publication (platform, status, external_post_id)
  schemas/
    auth.py               # DTOs auth & onboarding
    user.py               # DTO utilisateur
    social_account.py     # DTOs comptes sociaux & OAuth
    post.py               # DTOs posts, media, cibles
  services/
    auth_service.py               # Logique métier auth
    google_oauth_service.py       # OAuth Google (access token -> JWT)
    meta_oauth_service.py         # OAuth Meta (Facebook Pages + Instagram Business)
    instagram_oauth_service.py    # OAuth Instagram standalone
    linkedin_oauth_service.py     # OAuth LinkedIn
    storage_service.py            # Upload S3, presigned URLs, suppression
    publish_dispatcher.py         # Routage vers le bon service de publication
    meta_publish_service.py       # Publication Facebook Page
    instagram_publish_service.py  # Publication Instagram (image, vidéo, carousel)
    linkedin_publish_service.py   # Publication LinkedIn (UGC Posts + asset upload)
  workers/
    celery_app.py         # Configuration Celery + Beat schedule
    tasks.py              # refresh_expiring_tokens, publish_scheduled_post
```

## Schéma de base de données

| Table | Description |
|---|---|
| `users` | Utilisateurs (email, mot de passe hashé, google_id, onboarding_json) |
| `social_accounts` | Comptes sociaux connectés (tokens chiffrés, plateforme, external_id) |
| `media_assets` | Fichiers uploadés (S3 key, dimensions, durée) |
| `posts` | Posts (caption, content_type, scheduled_at, status) |
| `post_media` | Lien post <-> media avec ordre de tri |
| `post_targets` | Cibles de publication par plateforme |

Contrainte d'unicité : `(user_id, platform, external_id)` sur `social_accounts`.

## API Endpoints

### Authentification

| Méthode | URL | Description |
|---|---|---|
| `POST` | `/api/v1/auth/register` | Inscription (email + mot de passe + onboarding) |
| `POST` | `/api/v1/auth/login` | Connexion (OAuth2 form) |
| `POST` | `/api/v1/auth/google` | Connexion via Google (access token GIS) |

### Utilisateurs

| Méthode | URL | Description |
|---|---|---|
| `GET` | `/api/v1/users/me` | Profil courant (JWT requis) |

### Comptes sociaux

| Méthode | URL | Description |
|---|---|---|
| `GET` | `/api/v1/social-accounts` | Liste des comptes connectés |
| `GET` | `/api/v1/social-accounts/meta/connect` | URL de connexion Meta (Facebook + Instagram) |
| `GET` | `/api/v1/social-accounts/meta/callback` | Callback OAuth Meta |
| `GET` | `/api/v1/social-accounts/instagram/connect` | URL de connexion Instagram standalone |
| `GET` | `/api/v1/social-accounts/instagram/callback` | Callback OAuth Instagram |
| `GET` | `/api/v1/social-accounts/linkedin/connect` | URL de connexion LinkedIn |
| `GET` | `/api/v1/social-accounts/linkedin/callback` | Callback OAuth LinkedIn |

### Posts

| Méthode | URL | Description |
|---|---|---|
| `POST` | `/api/v1/posts` | Créer un post (draft ou scheduled) |
| `POST` | `/api/v1/posts/{post_id}/media` | Upload d'un média vers un post |
| `POST` | `/api/v1/posts/{post_id}/targets` | Ajouter une cible de publication |
| `POST` | `/api/v1/posts/{post_id}/publish` | Publier le post vers **toutes** ses cibles |
| `POST` | `/api/v1/post-targets/{post_target_id}/publish` | Publier vers une seule cible |

## OAuth : Configuration détaillée

### Meta (Facebook + Instagram)

Le flow Meta connecte simultanément toutes les Facebook Pages et leurs comptes Instagram Business associés.

1. Créer une app Meta Developers avec les produits **Facebook Login** et **Instagram Graph API**.
2. Scopes requis : `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `business_management`.
3. URL de redirection autorisée : `http://localhost:8000/api/v1/social-accounts/meta/callback`

Endpoints :

- `GET /api/v1/social-accounts/meta/connect` — génère l'URL d'autorisation
- `GET /api/v1/social-accounts/meta/callback?code=...&state=...` — échange le code, crée/upsert les comptes Facebook et Instagram

### Instagram standalone

Pour les comptes Instagram non liés à une Page Facebook.

1. Créer une app Instagram Basic Display ou Instagram Graph API.
2. Scopes : `instagram_business_basic`, `instagram_business_content_publish`.
3. URL de redirection autorisée : `http://localhost:8000/api/v1/social-accounts/instagram/callback`

Endpoints :

- `GET /api/v1/social-accounts/instagram/connect`
- `GET /api/v1/social-accounts/instagram/callback?code=...&state=...`

### LinkedIn

1. Créer une app LinkedIn Developer avec le produit **Sign In with LinkedIn**.
2. Scopes : `w_member_social`, `openid`, `profile`, `email`.
3. URL de redirection autorisée : `http://localhost:8000/api/v1/social-accounts/linkedin/callback`

Endpoints :

- `GET /api/v1/social-accounts/linkedin/connect`
- `GET /api/v1/social-accounts/linkedin/callback?code=...&state=...`

## Publication

### Formats supportés

| Plateforme | Texte seul | Image unique | Vidéo unique | Carousel / Multi-images |
|---|---|---|---|---|
| Facebook | Oui | Oui | Oui | Non |
| Instagram | Non | Oui | Oui (Reel) | Oui (carousel mixte image/vidéo) |
| LinkedIn | Oui | Oui (1+) | Oui (1 seule) | Non (pas de mix image/vidéo) |

### Flux de publication

1. Le frontend upload un média via `POST /posts/{post_id}/media` (stockage S3).
2. Le frontend ajoute des cibles via `POST /posts/{post_id}/targets`.
3. Publication :
   - `POST /posts/{post_id}/publish` publie vers toutes les cibles en parallèle (via `asyncio.gather`).
   - `POST /post-targets/{post_target_id}/publish` publie vers une seule cible.
4. Chaque cible est mise à jour indépendamment : `status`, `external_post_id`, `error_message`, `published_at`.

### Gestion des tokens

- Les tokens OAuth sont chiffrés en base (Fernet) avant stockage.
- Un worker Celery Beat rafraîchit quotidiennement (à 3h00) les tokens Meta/Instagram expirant dans les 7 jours.
- LinkedIn utilise un `refresh_token` stocké côté service.

## Workers

Lancer les workers séparément :

```bash
# Worker
celery -A app.workers.celery_app worker --loglevel=info

# Beat (planificateur)
celery -A app.workers.celery_app beat --loglevel=info
```

Tâches planifiées :

| Tâche | Schedule | Description |
|---|---|---|
| `refresh_expiring_tokens` | Tous les jours à 03:00 | Rafraîchit les tokens Meta/Instagram expirant sous 7 jours |

Tâches ad-hoc :

| Tâche | Description |
|---|---|
| `publish_scheduled_post(post_id)` | Publie un post programmé vers toutes ses cibles |

## Tests

Les tests sont autonomes (pas d'appel réseau externe) grâce à des mocks HTTP et SQLite en mémoire.

```bash
python scripts/test_meta_oauth_flow.py
python scripts/test_linkedin_oauth_flow.py
python scripts/test_http_smoke.py
```

- `test_meta_oauth_flow.py` : Flow complet Meta OAuth (mock httpx) — valide Facebook + Instagram.
- `test_linkedin_oauth_flow.py` : Flow complet LinkedIn OAuth (mock httpx).
- `test_http_smoke.py` : Vérifie le health HTTP via TestClient (register, /me, LinkedIn connect URL).

## Sécurité

- Mots de passe hashés avec bcrypt.
- JWT signés avec HS256.
- Tokens OAuth chiffrés avec Fernet avant stockage en base.
- Les endpoints de publication et de listing nécessitent un JWT valide (`get_current_user`).
- Pas de token ou de secret loggé en clair.

## État actuel du projet

**Fonctionnel et testé :**

- Auth classique (email/mot de passe) + Google Sign-In.
- OAuth Meta (Facebook Pages + Instagram Business) avec exchange de token long-lived.
- OAuth Instagram standalone.
- OAuth LinkedIn avec refresh token.
- Upload de médias vers un stockage S3-compatible.
- Publication vers Facebook (text, image, vidéo).
- Publication vers Instagram (image, Reel, carousel) avec polling de conteneur et retry.
- Publication vers LinkedIn (text, images, vidéo) via UGC Posts + asset upload.
- Publication parallèle multi-cibles avec gestion indépendante des erreurs.
- Rafraîchissement automatique des tokens Meta/Instagram (worker Celery Beat).
- Tests d'intégration mockés pour Meta et LinkedIn OAuth.

**Non encore implémenté / désactivé :**

- TikTok, YouTube (flags `ENABLE_TIKTOK`, `ENABLE_YOUTUBE` à `false`).
- Analytics, billing, IA (flags dédiés à `false`).
- Interface d'administration ou dashboard.
