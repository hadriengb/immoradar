# ImmoRadar

Veille des ventes immobilières aux enchères (Île-de-France, région lyonnaise), estimation de la valeur
de marché à partir des ventes réelles DVF, simulations achat-revente et locatif, classement des opportunités.

## Lancer

Prérequis : **Python 3.9 ou plus récent**. Aucune dépendance à installer (bibliothèque standard uniquement).

```bash
cd immoradar
python3 run.py            # puis ouvrir http://127.0.0.1:8765
```

Sous Windows : `py run.py`. Les données sont stockées dans `data/immoradar.db` (SQLite).
Options : `--port 8800`, `--search` (lance une recherche sans interface puis quitte : utile dans un cron).

Premier clic sur **🔎 LANCER UNE RECHERCHE** : la collecte prend quelques minutes (pause de 3 s entre deux
pages par politesse), puis les fichiers DVF des départements concernés sont téléchargés une fois pour toutes
(plusieurs dizaines de Mo par département, quelques minutes). Les recherches suivantes sont plus rapides.

## Ce que fait la recherche

1. Interroge chaque source autorisée (robots.txt relu à chaque fois, arrêt immédiat si CAPTCHA / protection).
2. Ajoute les nouvelles annonces, actualise les anciennes, fusionne les doublons entre sources.
3. Extrait surface, pièces, étage, mise à prix, date et lieu, occupation, annexes, niveau de travaux, risques.
4. Géolocalise l'adresse (API Adresse / Géoplateforme IGN).
5. Estime la valeur (DVF), le loyer (Carte des loyers ANIL 2025), calcule les deux simulations et les deux notes.
6. Affiche l'état de chaque source : `ok`, `vide`, `erreur`, `bloquée`, `manuel`, `désactivée`.

## Sources

| Source | Mode | Constat (26/11/2026) |
|---|---|---|
| Licitor | automatique | robots.txt autorise les pages d'annonces ; pas de clause interdisant la collecte ; pas de CAPTCHA constaté |
| Avoventes | automatique (expérimental) | robots.txt autorise tout ; structure HTML non vérifiée : collecteur générique |
| Immobilier des notaires | import manuel | CGU art. 5 : reproduction des annonces interdite |
| Enchères du Domaine | import manuel | application 100 % JavaScript, pas d'API publique |

## Fiabilité

- Aucune donnée n'est inventée : un champ non trouvé reste « inconnu ».
- Chaque valeur indique sa provenance (source, estimation, hypothèse, saisie manuelle) et la date de collecte.
- Pas d'estimation si moins de 6 comparables DVF (paramétrable) ou si surface / localisation manquent.
- Toutes les hypothèses de calcul sont visibles ligne par ligne et modifiables (Paramètres, ou par fiche).
- Un risque juridique majeur (démembrement, squat, péril, copropriété en difficulté) plafonne la note à 40.

## Tests

```bash
python3 -m unittest discover tests -v
```

Les tests utilisent un faux site local et des ventes DVF fictives, dans un dossier temporaire :
ils ne touchent jamais à la base de l'application.

## Structure

```
run.py                      lancement
immoradar/server.py         serveur HTTP + API JSON
immoradar/pipeline.py       recherche, déduplication, filtres, classement
immoradar/collectors/       un fichier par source
immoradar/extract.py        extraction des informations depuis le texte
immoradar/opendata.py       DVF, géocodage, Carte des loyers
immoradar/estimate.py       estimation par comparables
immoradar/finance.py        simulations achat-revente et locatif
immoradar/scoring.py        notes sur 100
static/                     interface (HTML/CSS/JS sans framework)
```

Données : DVF géolocalisées (DGFiP / Etalab), Carte des loyers (« Estimations ANIL, à partir des données du
Groupe SeLoger et de leboncoin »), Base Adresse Nationale — Licence Ouverte 2.0.

## Mise en ligne gratuite (Render + Neon)

L'offre gratuite de Render n'a pas de disque conservé : vos données (annonces, favoris, corrections,
réglages) sont donc stockées dans une base PostgreSQL gratuite Neon via `DATABASE_URL`. Les données DVF
et loyers restent dans un cache local, re-téléchargé automatiquement après un redémarrage.

1. Neon (neon.tech, gratuit, sans carte) : créer un projet, région Europe, copier la « connection string »
   (`postgresql://...?sslmode=require`).
2. Render (render.com, gratuit) : New > Blueprint > choisir le dépôt GitHub ImmoRadar. Renseigner
   `IMMORADAR_PASSWORD` (mot de passe de votre choix) et `DATABASE_URL` (la chaîne Neon).
3. Ouvrir l'URL `https://immoradar-xxxx.onrender.com`, identifiant `immoradar` + votre mot de passe.

Limites de l'offre gratuite : mise en veille après 15 min sans visite (réveil ~1 min) ; garder la page
ouverte pendant une recherche ; l'actualisation automatique ne tourne pas pendant la veille ; DVF
re-téléchargé après chaque réveil (quelques minutes à la première recherche). Les sites sources peuvent
refuser les adresses IP d'hébergeurs : l'état « bloquée » l'indiquera.

Sans `IMMORADAR_PASSWORD`, l'application refuse de démarrer sur une adresse publique.
