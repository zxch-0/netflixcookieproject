# netflixcookieproject

Vérificateur de cookies Netflix (Node.js + Express, zéro dépendance côté client).

## Lancer

    npm install
    npm start

Puis ouvrir http://localhost:3000

## Import

Trois façons d'importer des cookies, toutes fonctionnelles :

- glisser-déposer un ou plusieurs fichiers `.txt` / `.json` / `.csv` dans la zone d'import ;
- cliquer sur la zone pour ouvrir le sélecteur de fichiers ;
- coller le contenu dans le champ texte puis « Ajouter ».

Formats acceptés : Netscape (`domain\tflag\tpath\tsecure\texpiry\tname\tvalue`),
JSON (export d'extension navigateur), ou `name=value; name2=value2`.

Les cookies importés sont conservés dans le `localStorage` et réaffichés au rechargement.

## Variables d'environnement

| Nom | Défaut | Rôle |
| --- | --- | --- |
| `PORT` | `3000` | port d'écoute |
| `CHECK_CONCURRENCY` | `5` | vérifications simultanées |
| `CHECK_TIMEOUT_MS` | `20000` | délai max par cookie |
