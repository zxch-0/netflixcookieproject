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
| GET | `/api/version` | Version, limites, workers |
| GET | `/api/config` | Limites (protégé par jeton) |
| POST | `/api/parse` | `{"text": "..."}` → comptes normalisés |
| POST | `/api/check` | `{"accounts": [...]}` → résultats d'un lot |
| POST | `/api/check-text` | Parse + vérifie le premier lot |

L'interface découpe automatiquement la vérification en lots pour rester sous le
timeout du proxy.

## Configuration

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `ACCESS_TOKEN` | vide | Jeton partagé ; vide = accès libre |
| `CHECK_WORKERS` | `12` | Threads de vérification |
| `CHECK_MAX_CONNECTIONS` | `24` | Taille du pool HTTP |
| `CHECK_CONNECT_TIMEOUT` | `5` | Timeout de connexion (s) |
| `CHECK_READ_TIMEOUT` | `12` | Timeout de lecture (s) |
| `MAX_ACCOUNTS_PER_REQUEST` | `25` | Comptes par lot |
| `MAX_PASTE_CHARS` | `2000000` | Taille max d'un collage |
| `PORT` | `8000` | Port d'écoute |

## Développement

    pip install -r requirements.txt
    uvicorn app:app --reload
    pytest

## Déploiement

Le fichier `render.yaml` décrit le service (build, démarrage, healthcheck). Un
`Dockerfile` est fourni pour tout autre hébergeur. Pensez à définir
`ACCESS_TOKEN` pour restreindre l'accès public.
