"""Immobilier des notaires — ventes aux enchères notariales (adjudication, 36h-immo / immo-interactif).

Constat du 26/11/2026 : robots.txt (Crawl-delay 10) n'interdit pas les pages d'annonces, MAIS les CGU
(art. 5) interdisent de « reproduire, à des fins commerciales ou non, des informations ou des annonces
présentes sur le Site » et de copier des informations permettant de reconstituer les fichiers d'origine ;
les mentions légales (art. 4) interdisent toute reproduction sans autorisation écrite.
=> Pas de collecte automatique. ImmoRadar fournit les liens de recherche par département et un import manuel.
"""
from ..geo_fr import DEPARTEMENTS, slugify

NAME = "notaires"
LABEL = "Immobilier des notaires"
HOME = "https://www.immobilier.notaires.fr"
MODE = "manuel"
LEGAL = ("Collecte automatique non autorisée : les CGU (art. 5) interdisent la reproduction des annonces. "
         "Liens de recherche directs + import manuel.")


def manual_links(deps):
    out = []
    for d in deps:
        slug = f"{slugify(DEPARTEMENTS.get(d, d))}-{d.lower()}"
        out.append({"label": f"Notaires — adjudications {DEPARTEMENTS.get(d, d)}",
                    "url": f"{HOME}/fr/annonces-immobilieres/adjudication/appartement/{slug}"})
        out.append({"label": f"Notaires — ventes interactives {DEPARTEMENTS.get(d, d)}",
                    "url": f"{HOME}/fr/annonces-immobilieres/immo-interactif/appartement/{slug}"})
    return out
