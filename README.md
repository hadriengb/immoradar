# ImmoRadar

Veille des ventes immobilières aux enchères (Île-de-France, région lyonnaise), estimation de la valeur
de marché à partir des ventes réelles DVF, simulations achat-revente et locatif, classement des opportunités.

## Lancer

**Mac** : double-cliquez sur `Lancer ImmoRadar.command` (la première fois : clic droit > Ouvrir > Ouvrir).
Le navigateur s'ouvre tout seul. Gardez la fenêtre du Terminal ouverte ; fermez-la pour arrêter.
Si macOS bloque le fichier (macOS 15 et plus : « Apple n'a pas pu vérifier… ») : Réglages Système > Confidentialité et
sécurité > « Ouvrir quand même », ou dans le Terminal : `xattr -dr com.apple.quarantine ~/Downloads/ImmoRadar*`.
Sans le lanceur : `cd` dans le dossier décompressé puis `python3 run.py`.

Autre système : `python3 run.py` dans le dossier (Python 3.9+, aucune dépendance), puis http://127.0.0.1:8765.

Vos données sont dans `~/ImmoRadar-donnees` : elles sont conservées quand vous installez une nouvelle version,
et celles d'une version précédente (dossier `data/` d'un ancien zip dans Téléchargements) sont reprises automatiquement.
Si le port 8765 est déjà pris (ancienne version encore ouverte), un autre port est choisi et affiché.
Tester la lecture d'un PDF : `python3 run.py --test-pdf chemin/vers/document.pdf`.

Sous Windows : `py run.py`. Les données sont stockées dans `data/immoradar.db` (SQLite).
Options : `--port 8800`, `--search` (lance une recherche sans interface puis quitte : utile dans un cron).

Premier clic sur **🔎 LANCER UNE RECHERCHE** : la collecte prend quelques minutes (pause de 3 s entre deux
pages par politesse), puis les fichiers DVF des départements concernés sont téléchargés une fois pour toutes
(plusieurs dizaines de Mo par département, quelques minutes). Les recherches suivantes sont plus rapides.

## Recherche automatique du matin

Par défaut, une recherche part chaque matin à partir de 7 h (heure réglable dans Paramètres) tant qu'ImmoRadar est
ouvert ; s'il était fermé, elle part dès l'ouverture. Sur Mac, « Activer aussi quand l'application est fermée »
(Paramètres) installe une tâche du matin dans votre session (LaunchAgent), sans Terminal. Une seule recherche par jour.
Les nouvelles annonces s'affichent en tête du tableau de bord jusqu'à « Marquer comme vues ».

Si une source répond mal (coupure, surcharge), ImmoRadar réessaie deux fois en ralentissant, garde ce qu'il a lu,
puis s'arrête sans insister ; les annonces déjà enregistrées restent affichées. Pour une source « vide » ou en
erreur, le bouton « Créer un diagnostic » du tableau de bord enregistre les pages reçues dans un zip à transmettre.

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
| Vench | automatique | robots.txt n'exclut que /upload/ ; mentions légales sans clause interdisant la collecte ; parties réservées aux abonnés non lues, emails masqués non décodés |
| Avoventes | automatique (expérimental) | robots.txt autorise tout ; structure HTML non vérifiée : `python3 run.py --diagnostic` enregistre les pages reçues pour écrire un vrai lecteur |
| Immobilier des notaires | import manuel | CGU art. 5 : reproduction des annonces interdite |
| Info-Enchères | import manuel | CGU : usage strictement personnel, copie et reproduction interdites |
| Annonces classiques (SeLoger, Leboncoin, PAP, Bien'ici) | import manuel | Leboncoin interdit expressément les robots ; robots.txt de PAP et Bien'ici fermés aux annonces ; SeLoger refuse les robots. Import d'une annonce ouverte soi-même (lien + texte), analysée comme vente classique |
| Enchères du Domaine | import manuel | application 100 % JavaScript avec cookies obligatoires, pas d'API publique |

Import manuel : collez le lien de l'annonce. Pour Licitor, Vench et Avoventes le lien suffit ; pour les autres,
collez aussi le texte de la page (Cmd+A, Cmd+C).

## Fiabilité

- Aucune donnée n'est inventée : un champ non trouvé reste « inconnu ».
- Chaque valeur indique sa provenance (source, estimation, hypothèse, saisie manuelle) et la date de collecte.
- Pas d'estimation si moins de 6 comparables DVF (paramétrable) ou si surface / localisation manquent.
- Toutes les hypothèses de calcul sont visibles ligne par ligne et modifiables (Paramètres, ou par fiche).
- Un risque juridique majeur (démembrement, squat, péril, copropriété en difficulté) plafonne la note à 40.
- Vue « Sélection » (par défaut) : vente à venir, sans risque juridique majeur, note ≥ 60 (réglable) ; le nombre de biens écartés et la raison sont affichés.
- « Opportunité intéressante » = note ≥ 80 (réglable). Classement par défaut : 70 % meilleure note + 30 % complétude du dossier (PV, cahier, diagnostics lus, estimation, données connues).

## Documents de vente (PV descriptif, cahier des conditions de vente, diagnostics)

Sur chaque fiche, « Ce que disent les documents » :
- **Analyser les documents liés** : télécharge les PDF liés à l'annonce si le robots.txt du site l'autorise
  (sinon l'application le dit et vous déposez le PDF vous-même) ; la recherche le fait aussi automatiquement
  (15 annonces au plus par recherche) ;
- **Déposer un PDF** ou **coller le texte** d'un document.

Ce qui est extrait (avec page et citation) : occupation (prime sur l'annonce), état général et nombre de défauts
relevés (niveau de travaux), surface Carrez, vente en un seul lot, faculté de baisse de la mise à prix, frais
préalables, charges et taxe foncière, DPE (G/F : risque pour la location), liquidation judiciaire, vente annulée
par le passé, résidence sociale, bien décrit comme « chambre », hypothèques, servitudes, amiante/plomb/termites…
Les prix ou comparables cités dans le document sont affichés mais ne remplacent jamais l'estimation DVF.
Les écarts entre l'annonce et le document (surface, mise à prix, occupation) sont signalés.

Lecture des PDF :
- **macOS : rien à installer.** ImmoRadar utilise PDFKit (texte) et Vision (OCR des scans, français) intégrés au système.
- Linux / Docker (Render) : `pdftotext` (poppler) et `tesseract` + français, inclus dans l'image Docker.
- Sinon, un PDF non lisible est signalé : collez son texte sur la fiche.

## Contacts et mail de demande d'informations

Sur chaque fiche, « Contacts et demande d'informations » : avocat(s) ou notaire, cabinet, adresse, téléphone, site,
email et référence greffe du cahier, lus dans l'annonce (jamais inventés, source indiquée). La liste de ce qui manque
(PV, cahier, diagnostics, charges, taxe foncière, occupation, frais préalables…) produit un mail prêt à envoyer :
« Ouvrir dans ma messagerie » ou « Copier le message ». ImmoRadar n'envoie rien lui-même. Signature : Paramètres.

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
