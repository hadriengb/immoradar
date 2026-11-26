"""Annonces classiques (SeLoger, Leboncoin, PAP, Bien'ici) — ventes de gré à gré, pas des enchères.

Constat du 26/11/2026 :
- Leboncoin : l'en-tête du robots.txt indique « It's forbidden to use search robots or other automatic methods
  to access Leboncoin.fr » (accès seulement avec autorisation spéciale) ; /recherche et /ad/ interdits aux robots nommés.
- PAP : robots.txt interdit les pages de liste et de recherche (/annonce/liste/, /*?*…) et bloque les clients
  « python-requests », « libwww »…
- Bien'ici : robots.txt interdit /annonces-* ; site en JavaScript.
- SeLoger : le site a refusé la lecture automatique de son robots.txt.
=> Aucune collecte automatique. L'utilisateur colle le lien et le texte d'une annonce qu'il a ouverte lui-même ;
   elle est alors analysée comme une vente classique (prix affiché, frais de notaire, pas de hausse d'enchères).
"""
NAME = "classiques"
LABEL = "Annonces classiques (SeLoger, Leboncoin, PAP, Bien'ici)"
HOME = "https://www.seloger.com"
MODE = "manuel"
DOMAINS = {"seloger.com": "seloger", "leboncoin.fr": "leboncoin", "pap.fr": "pap", "bienici.com": "bienici"}
LEGAL = ("Collecte automatique interdite ou refusée (Leboncoin l'interdit expressément, robots.txt de PAP et Bien'ici "
         "fermés aux pages d'annonces, SeLoger refuse les robots). Import d'une annonce ouverte par vous : lien + texte collé.")


def manual_links(deps):
    return [{"label": "SeLoger", "url": "https://www.seloger.com/"},
            {"label": "Leboncoin — ventes immobilières", "url": "https://www.leboncoin.fr/c/ventes_immobilieres"},
            {"label": "PAP", "url": "https://www.pap.fr/"},
            {"label": "Bien'ici", "url": "https://www.bienici.com/"}]
