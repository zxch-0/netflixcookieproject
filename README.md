# Netflix Cookie Checker

Service web qui vérifie en masse des cookies Netflix et remonte le plan, le pays
et les profils associés.

Déploiement : https://netflix-dispo-checker.onrender.com/

## Fonctionnement

Un compte est **valide** quand `GET https://www.netflix.com/account` répond `200`
sans rebond vers `/login`. Les redirections sont suivies manuellement : seul un
rebond vers la page de connexion (ou la présence du formulaire de login) marque
le cookie comme invalide. Les autres codes (403, 429, 5xx, timeout) sont
rapportés comme erreurs, jamais comme succès.

## Formats acceptés

- Netscape (`domain  TRUE  /  TRUE  expiry  name  value`)
- JSON (`[{"name": ..., "value": ...}]`, `{"cookies": [...]}` ou mapping plat)
- En-tête brut (`NetflixId=...; SecureNetflixId=...`)

Plusieurs comptes peuvent être collés d'un coup : séparateurs `---` / `###` /
`===`, lignes vides, ou répétitions de `NetflixId=`. Les doublons sont retirés.

## API

| Méthode | Route | Description |
| --- | --- | --- |
| GET | `/` | Interface |
| GET | `/healthz` | Sonde de santé |
| GET | `/api/config` | Limites, plans et devices |
| POST | `/api/jobs` | `{"cookies": "..."}` → job asynchrone |
| POST | `/api/jobs/files` | Fichiers `.txt` / `.json` / `.csv` / `.zip` → job |
| GET | `/api/jobs/{job_id}` | Progression et résultats triés par plan |
| GET | `/api/jobs/{job_id}/report.txt` | Rapport texte |
| GET | `/api/jobs/{job_id}/report.json` | Rapport JSON |
| DELETE | `/api/jobs/{job_id}` | Annule un job |

L'interface découpe automatiquement la vérification en lots pour rester sous le
timeout du proxy.

## Configuration

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `ACCESS_TOKEN` | vide | Jeton partagé ; vide = accès libre |
| `PROXY_LIST` | vide | Proxys serveur (`host:port:user:pass`, un par ligne) |
| `DEFAULT_CONCURRENCY` | `8` | Workers par défaut |
| `MAX_CONCURRENCY` | `32` | Workers max par job |
| `MAX_COOKIES_PER_JOB` | `250` | Comptes max par job |
| `MAX_ACTIVE_JOBS` | `8` | Jobs simultanés |
| `JOB_TTL_SECONDS` | `3600` | Rétention des jobs (s) |
| `REQUEST_TIMEOUT_SECONDS` | `12` | Timeout HTTP (s) |
| `CHECK_RETRIES` | `2` | Tentatives par cookie |
| `NFTOKEN_ATTEMPTS` | `3` | Tentatives NFToken |
| `NFTOKEN_ENABLED` | `true` | Génère les liens NFToken |
| `STATE_DIR` | `/tmp/netflix-dispo-checker` | Snapshots des jobs |
| `PORT` | `8000` | Port d'écoute |

## Développement

    pip install -r requirements-dev.txt
    uvicorn app.main:app --reload
    pytest

## Déploiement

Le fichier `render.yaml` décrit le service (build, démarrage, healthcheck). Un
`Dockerfile` est fourni pour tout autre hébergeur. Pensez à définir
`ACCESS_TOKEN` pour restreindre l'accès public.

Le package `app` est le point d'entrée ASGI : `uvicorn app.main:app` (ou
`uvicorn app:app`, ré-exporté par `app/__init__.py`). Le module legacy `app.py`
a été supprimé pour éviter le conflit de nom avec le package.
