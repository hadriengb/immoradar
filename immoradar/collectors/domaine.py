"""Enchères du Domaine (DGFiP) — ventes domaniales.

Constat du 26/11/2026 : le site est une application JavaScript qui exige JavaScript et cookies ;
aucune page ni robots.txt exploitable n'est servi sans navigateur, aucune API publique documentée
n'a été trouvée. Exécuter un navigateur automatisé pour contourner cette exigence n'est pas retenu.
=> Import manuel (lien direct vers le site).
"""
NAME = "domaine"
LABEL = "Enchères du Domaine"
HOME = "https://encheres-domaine.gouv.fr"
MODE = "manuel"
LEGAL = ("Site 100 % JavaScript sans API publique ni robots.txt lisible : pas de collecte automatique. "
         "Lien direct + import manuel.")


def manual_links(deps):
    return [{"label": "Enchères du Domaine — accueil", "url": HOME + "/"}]
